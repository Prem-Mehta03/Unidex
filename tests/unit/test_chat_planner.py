import pytest

from tests.unit._catalog_data import ALIASES, chat_views
from unidex.chat.models import Interpretation, Material
from unidex.chat.planner import ChatPlanner
from unidex.search.catalog import Catalog

OOP = ("CS F213",)
DD = ("CS F215",)


@pytest.fixture(scope="module")
def planner() -> ChatPlanner:
    return ChatPlanner(Catalog(chat_views(), course_aliases=ALIASES))


def names(result) -> list[str]:
    return [hit.doc.name for stack in result.stacks for hit in stack.hits]


def test_papers_use_exam_type_filter(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(courses=OOP, exam_types=("compre",), materials=(Material.PAPERS,))
    )
    assert sorted(names(result)) == ["OOP Compre 2022.pdf", "OOP Compre 2023.pdf"]


def test_last_n_years_counts_years_that_have_that_exam(planner: ChatPlanner) -> None:
    # DD compre exists in 2024 and 2025 only; "last 3 years" must not reach back to years
    # without compre papers.
    result = planner.run(
        Interpretation(
            courses=DD, exam_types=("compre",), materials=(Material.PAPERS,), recent_years=3
        )
    )
    assert sorted(names(result)) == ["DD Compre 2024.pdf", "DD Compre 2025.pdf"]
    assert result.paper_years == (2025, 2024)


def test_last_one_year_is_the_newest_year_with_that_exam(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(
            courses=OOP, exam_types=("midsem",), materials=(Material.PAPERS,), recent_years=1
        )
    )
    assert names(result) == ["OOP Midsem 2024.pdf"]


def test_explicit_years(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(
            courses=OOP, exam_types=("midsem",), materials=(Material.PAPERS,), years=(2021, 2023)
        )
    )
    assert sorted(names(result)) == ["OOP Midsem 2021.pdf", "OOP Midsem 2023.pdf"]


def test_solutions_are_separate_from_papers(planner: ChatPlanner) -> None:
    only_solutions = planner.run(Interpretation(courses=OOP, materials=(Material.SOLUTIONS,)))
    assert names(only_solutions) == ["OOP Midsem 2022 Solutions.pdf"]


def test_quiz_and_test_are_treated_as_related(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(courses=OOP, exam_types=("quiz",), materials=(Material.PAPERS,))
    )
    assert sorted(names(result)) == ["OOP Quiz 1.pdf", "OOP Quiz 2.pdf"]


def test_quiz_number_filters_further(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(
            courses=OOP, exam_types=("quiz",), materials=(Material.PAPERS,), quiz_number=2
        )
    )
    assert names(result) == ["OOP Quiz 2.pdf"]
    missing = planner.run(
        Interpretation(
            courses=OOP, exam_types=("quiz",), materials=(Material.PAPERS,), quiz_number=9
        )
    )
    assert missing.total_files == 0
    assert any("number 9" in note for note in missing.notes)


def test_topics_search_slides_by_name_not_papers(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(
            courses=OOP,
            exam_types=("compre",),
            materials=(Material.PAPERS, Material.SLIDES),
            topics=("inheritance",),
        )
    )
    found = names(result)
    assert "Inheritance Lecture 3.pdf" in found
    assert "Polymorphism Lecture 4.pdf" not in found  # does not mention the topic
    assert "OOP Compre 2022.pdf" in found  # papers are not filtered by topic
    assert any("cannot be searched by topic" in note for note in result.notes)


def test_unmatched_topics_fall_back_to_all_material_and_say_so(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(courses=OOP, materials=(Material.SLIDES,), topics=("quantum gravity",))
    )
    assert len(names(result)) == 3
    assert any("No lecture slides file names mention your topics" in n for n in result.notes)


def test_midsem_only_hides_post_midsem_slides(planner: ChatPlanner) -> None:
    midsem = planner.run(
        Interpretation(courses=OOP, exam_types=("midsem",), materials=(Material.SLIDES,))
    )
    assert "Generics Lecture 9.pdf" not in names(midsem)
    assert any("post-midsem" in note for note in midsem.notes)
    compre = planner.run(
        Interpretation(courses=OOP, exam_types=("compre",), materials=(Material.SLIDES,))
    )
    assert "Generics Lecture 9.pdf" in names(compre)
    both = planner.run(
        Interpretation(courses=OOP, exam_types=("midsem", "compre"), materials=(Material.SLIDES,))
    )
    assert "Generics Lecture 9.pdf" in names(both)


def test_notes_cover_cheatsheets(planner: ChatPlanner) -> None:
    result = planner.run(Interpretation(courses=OOP, materials=(Material.NOTES,)))
    assert names(result) == ["Java Cheatsheet.pdf"]


def test_years_apply_to_other_material_only_when_no_papers_are_wanted(planner: ChatPlanner) -> None:
    only_slides = planner.run(
        Interpretation(courses=DD, materials=(Material.NOTES,), years=(2024,))
    )
    assert only_slides.total_files == 0
    with_papers = planner.run(
        Interpretation(courses=DD, materials=(Material.NOTES, Material.PAPERS), years=(2024,))
    )
    assert "Karnaugh Maps Notes.pdf" in names(with_papers)


def test_empty_result_explains_which_years_exist(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(
            courses=OOP, exam_types=("midsem",), materials=(Material.PAPERS,), years=(2050,)
        )
    )
    assert result.total_files == 0
    assert any("2024-25" in note for note in result.notes)


def test_course_without_papers(planner: ChatPlanner) -> None:
    result = planner.run(Interpretation(courses=("CS F999",), materials=(Material.PAPERS,)))
    assert result.total_files == 0
    assert any("No past papers are indexed" in note for note in result.notes)


def test_stacks_are_grouped_and_counted(planner: ChatPlanner) -> None:
    result = planner.run(Interpretation(courses=OOP, exam_types=("midsem",)))
    assert result.total_files == sum(len(s.hits) for s in result.stacks)
    titles = {s.title for s in result.stacks}
    assert "Object Oriented Programming · Midsem papers" in titles


def test_two_courses_at_once(planner: ChatPlanner) -> None:
    result = planner.run(
        Interpretation(courses=OOP + DD, exam_types=("compre",), materials=(Material.PAPERS,))
    )
    assert {h.doc.course_code for s in result.stacks for h in s.hits} == {"CS F213", "CS F215"}
