import pytest

from tests.unit._catalog_data import ALIASES, chat_views
from unidex.chat.models import DEFAULT_MATERIALS, Interpretation, Material
from unidex.chat.parser import RuleParser
from unidex.search.catalog import Catalog

P, S, N, SL, T = (
    Material.PAPERS,
    Material.SOLUTIONS,
    Material.NOTES,
    Material.SLIDES,
    Material.TUTORIALS,
)


@pytest.fixture(scope="module")
def parser() -> RuleParser:
    return RuleParser(Catalog(chat_views(), course_aliases=ALIASES).scan_courses)


def parse(
    parser: RuleParser, message: str, previous: Interpretation | None = None
) -> Interpretation:
    return parser.parse(message, previous).interpretation


def test_course_exam_and_material(parser: RuleParser) -> None:
    result = parse(parser, "OOP midsem papers and notes")
    assert result.courses == ("CS F213",)
    assert result.exam_types == ("midsem",)
    assert set(result.materials) == {P, N}
    assert result.topics == ()


def test_course_full_name_and_code_work(parser: RuleParser) -> None:
    assert parse(parser, "object oriented programming compre").courses == ("CS F213",)
    assert parse(parser, "CS F215 compre papers").courses == ("CS F215",)


def test_topics_come_from_the_leftover_words(parser: RuleParser) -> None:
    result = parse(
        parser,
        "I want everything for OOP midsem preparation, "
        "specifically inheritance and polymorphism notes",
    )
    assert result.topics == ("inheritance", "polymorphism")
    assert set(result.materials) == set(Material)


def test_recognised_words_never_become_topics(parser: RuleParser) -> None:
    result = parse(parser, "please show me dd compre pyqs and solutions")
    assert result.topics == ()
    assert set(result.materials) == {P, S}


@pytest.mark.parametrize(
    ("message", "years", "recent"),
    [
        ("dd compre last 3 years", (), 3),
        ("dd compre past two years", (), 2),
        ("dd compre papers from last year", (), 1),
        ("dd compre 2024", (2024,), None),
        ("dd compre 2023-24", (2023,), None),
        ("dd compre 22-23", (2022,), None),
        ("dd compre 2022, 2023", (2022, 2023), None),
        ("dd compre last 50 years", (), 10),
    ],
)
def test_years(
    parser: RuleParser, message: str, years: tuple[int, ...], recent: int | None
) -> None:
    result = parse(parser, message)
    assert result.years == years
    assert result.recent_years == recent
    assert result.topics == ()


def test_single_year_gets_an_explanatory_note(parser: RuleParser) -> None:
    notes = parser.parse("dd compre 2024").notes
    assert any("2024-25" in note for note in notes)


def test_not_a_year_span_is_kept_as_text(parser: RuleParser) -> None:
    assert "10-15" in " ".join(parse(parser, "dd notes on pages 10-15").topics)


@pytest.mark.parametrize(
    ("message", "exams"),
    [
        ("oop mid sem", ("midsem",)),
        ("oop mid-semester papers", ("midsem",)),
        ("oop comprehensive", ("compre",)),
        ("oop end sem", ("compre",)),
        ("oop lab compre", ("lab_compre",)),
        ("oop quizzes", ("quiz",)),
        ("oop midsem and compre", ("compre", "midsem")),
    ],
)
def test_exam_words(parser: RuleParser, message: str, exams: tuple[str, ...]) -> None:
    assert set(parse(parser, message).exam_types) == set(exams)


def test_quiz_number(parser: RuleParser) -> None:
    result = parse(parser, "oop quiz 2 solutions")
    assert result.exam_types == ("quiz",)
    assert result.quiz_number == 2
    assert parse(parser, "oop q3").quiz_number == 3
    assert parse(parser, "oop q3").exam_types == ("quiz",)


def test_naming_a_course_alone_uses_default_materials(parser: RuleParser) -> None:
    result = parse(parser, "oop")
    assert result.courses == ("CS F213",)
    assert result.materials == DEFAULT_MATERIALS


def test_unknown_course_leaves_courses_empty(parser: RuleParser) -> None:
    result = parser.parse("midsem on Stochastic Calculus and Finance, need PYQ and notes")
    assert result.interpretation.courses == ()
    assert result.understood is True
    assert "stochastic calculus" in result.interpretation.topics


@pytest.mark.parametrize(
    "message", ["hi", "Hello!", "thanks", "thank you", "help", "what can you do"]
)
def test_smalltalk(parser: RuleParser, message: str) -> None:
    assert parser.parse(message).smalltalk


@pytest.mark.parametrize("message", ["", "   ", "the of and", "please show me"])
def test_nothing_usable(parser: RuleParser, message: str) -> None:
    result = parser.parse(message)
    assert result.understood is False
    assert result.interpretation.is_blank()


class TestRefinement:
    @pytest.fixture
    def first(self, parser: RuleParser) -> Interpretation:
        return parse(parser, "oop midsem papers and solutions inheritance")

    def test_only_replaces_the_fields_it_mentions(
        self, parser: RuleParser, first: Interpretation
    ) -> None:
        result = parse(parser, "show only 2022 solutions", first)
        assert result.courses == first.courses
        assert result.exam_types == ("midsem",)
        assert result.materials == (S,)
        assert result.years == (2022,)
        assert result.topics == ("inheritance",)

    def test_also_adds_to_the_old_values(self, parser: RuleParser, first: Interpretation) -> None:
        result = parse(parser, "also include generics and notes", first)
        assert result.topics == ("inheritance", "generics")
        assert set(result.materials) == {P, S, N}

    def test_a_new_course_replaces_the_old_one(
        self, parser: RuleParser, first: Interpretation
    ) -> None:
        assert parse(parser, "dd", first).courses == ("CS F215",)

    def test_start_over_forgets_everything(self, parser: RuleParser, first: Interpretation) -> None:
        result = parse(parser, "start over, dd compre", first)
        assert result.courses == ("CS F215",)
        assert result.topics == ()
        assert result.exam_types == ("compre",)

    def test_a_clarification_answer_completes_the_form(self, parser: RuleParser) -> None:
        asked = parse(parser, "midsem papers and notes on inheritance")
        assert asked.courses == ()
        answered = parse(parser, "OOP", asked)
        assert answered.courses == ("CS F213",)
        assert answered.exam_types == ("midsem",)
        assert answered.topics == ("inheritance",)

    def test_new_year_phrase_replaces_old_years(
        self, parser: RuleParser, first: Interpretation
    ) -> None:
        with_year = parse(parser, "2022", first)
        assert parse(parser, "last 2 years", with_year).years == ()
        assert parse(parser, "last 2 years", with_year).recent_years == 2


def test_oversized_message_is_cut(parser: RuleParser) -> None:
    result = parser.parse("oop " + "word " * 1000)
    assert result.interpretation.courses == ("CS F213",)
    assert len(result.interpretation.topics) <= 8


def test_topic_phrases_are_bounded(parser: RuleParser) -> None:
    result = parse(parser, "oop " + ", ".join(f"topic{i}x" for i in range(30)))
    assert len(result.topics) == 8
    long_phrase = parse(parser, "oop " + "a" * 100)
    assert long_phrase.topics == ()


@pytest.mark.parametrize(
    ("message", "material"),
    [
        ("dd compre sol", S),
        ("dd practice problems", T),
        ("dd problem sets", T),
        ("dd previous year papers", P),
        ("dd answer key", S),
    ],
)
def test_material_slang(parser: RuleParser, message: str, material: Material) -> None:
    result = parse(parser, message)
    assert result.materials == (material,)
    assert result.topics == ()


@pytest.mark.parametrize(
    "message", ["dd compre papers latest", "newest dd compre", "dd most recent compre"]
)
def test_latest_alone_means_one_recent_year(parser: RuleParser, message: str) -> None:
    result = parser.parse(message)
    assert result.interpretation.recent_years == 1
    assert result.interpretation.topics == ()
    assert any("latest" in note for note in result.notes)
