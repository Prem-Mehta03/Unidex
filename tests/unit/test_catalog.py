import pytest

from tests.unit._catalog_data import make_view, sample_views
from unidex.search.catalog import Catalog, SearchFilters, parse_years


@pytest.fixture
def catalog() -> Catalog:
    return Catalog(sample_views())


def names(result) -> list[str]:
    return [hit.doc.name for hit in result.hits]


def test_text_search_ranks_by_bm25(catalog: Catalog) -> None:
    result = catalog.search("laplace")
    assert names(result) == ["Laplace Transform Notes.pdf"]
    assert result.total == 1


def test_no_query_and_no_filters_returns_nothing_but_describes_catalog(catalog: Catalog) -> None:
    result = catalog.search("")
    assert result.hits == []
    assert result.total == 0
    assert sum(o.count for o in result.facets["doc_type"]) == len(catalog)


def test_stopword_only_query_counts_as_empty(catalog: Catalog) -> None:
    assert catalog.search("the of").hits == []


def test_filter_only_browse_is_newest_first(catalog: Catalog) -> None:
    result = catalog.search(
        "", SearchFilters(courses=frozenset({"CS F213"}), doc_types=frozenset({"pyq"}))
    )
    years = [hit.doc.academic_year for hit in result.hits]
    assert years == sorted(years, reverse=True)
    assert all(hit.score == 0.0 for hit in result.hits)


def test_filters_restrict_text_search(catalog: Catalog) -> None:
    unfiltered = catalog.search("midsem")
    only_oop = catalog.search("midsem", SearchFilters(courses=frozenset({"CS F213"})))
    assert {h.doc.course_code for h in unfiltered.hits} == {"CS F213", "MATH F211"}
    assert {h.doc.course_code for h in only_oop.hits} == {"CS F213"}
    assert only_oop.total < unfiltered.total


def test_values_within_a_facet_are_or_and_facets_are_and(catalog: Catalog) -> None:
    both_exams = catalog.search(
        "",
        SearchFilters(courses=frozenset({"CS F213"}), exam_types=frozenset({"midsem", "compre"})),
    )
    assert {h.doc.exam_type for h in both_exams.hits} == {"midsem", "compre"}
    year_2022 = catalog.search(
        "", SearchFilters(courses=frozenset({"CS F213"}), years=frozenset({2022}))
    )
    assert names(year_2022) == ["OOP Midsem 2022.pdf"]


def test_unknown_filter_value_matches_nothing(catalog: Catalog) -> None:
    result = catalog.search("midsem", SearchFilters(courses=frozenset({"NOPE F000"})))
    assert result.hits == []
    assert result.total == 0


def test_facet_counts_ignore_the_facets_own_filter(catalog: Catalog) -> None:
    only_oop = catalog.search("midsem", SearchFilters(courses=frozenset({"CS F213"})))
    courses = {o.value: o.count for o in only_oop.facets["course"]}
    assert set(courses) == {"CS F213", "MATH F211"}  # the other course stays pickable
    doc_types = {o.value: o.count for o in only_oop.facets["doc_type"]}
    assert doc_types == {"pyq": 2, "solution": 1}  # counts respect the course filter


def test_exam_type_none_and_unknown_are_not_offered(catalog: Catalog) -> None:
    values = {o.value for o in catalog.facet_options("exam_type")}
    assert values == {"midsem", "compre", "quiz"}


def test_matched_terms_report_which_words_hit(catalog: Catalog) -> None:
    hit = catalog.search("laplace pizza").hits[0]
    assert hit.matched_terms == ("laplace",)


def test_max_hits_caps_hits_but_not_total(catalog: Catalog) -> None:
    result = catalog.search("midsem", max_hits=2)
    assert len(result.hits) == 2
    assert result.total > 2


def test_year_labels_and_order(catalog: Catalog) -> None:
    options = catalog.facet_options("year")
    assert [o.value for o in options] == ["2025", "2023", "2022"]
    assert options[0].label == "2025-26"


def test_course_label_includes_name(catalog: Catalog) -> None:
    labels = {o.value: o.label for o in catalog.facet_options("course")}
    assert labels["CS F213"] == "CS F213 · Object Oriented Programming"


def test_filters_without_and_values() -> None:
    filters = SearchFilters(courses=frozenset({"A"}), years=frozenset({2023}))
    assert filters.without("course").is_empty() is False
    assert filters.without("course").without("year").is_empty()
    with pytest.raises(ValueError, match="Unknown facet"):
        filters.values("colour")


def test_suggest_completes_words(catalog: Catalog) -> None:
    assert "laplace" in catalog.suggest("lap")
    assert catalog.suggest("") == []


def test_empty_catalog_does_not_crash() -> None:
    empty = Catalog([])
    assert empty.search("anything").hits == []
    assert len(empty) == 0


def test_parse_years() -> None:
    assert parse_years(["2023", "2022"]) == frozenset({2022, 2023})
    with pytest.raises(ValueError, match="out of range"):
        parse_years(["1999"])


def test_documents_without_year_are_still_searchable() -> None:
    catalog = Catalog([make_view("Orphan.pdf", year=None, course_code="", course_name="")])
    assert names(catalog.search("orphan")) == ["Orphan.pdf"]
    assert catalog.facet_options("year") == []
    assert catalog.facet_options("course") == []


ALIASES = {"oop": "CS F213", "m3": "MATH F211", "logicincs": "CS F214", "ghost": "CS F999"}


@pytest.fixture
def aliased() -> Catalog:
    return Catalog(sample_views(), course_aliases=ALIASES)


def test_nickname_in_query_becomes_a_course_filter(aliased: Catalog) -> None:
    result = aliased.search("oop midsem")
    assert result.detected_courses == ("CS F213",)
    assert {hit.doc.course_code for hit in result.hits} == {"CS F213"}
    assert "oop" not in {term for hit in result.hits for term in hit.matched_terms}


def test_nickname_alone_browses_the_whole_course(aliased: Catalog) -> None:
    result = aliased.search("oop")
    assert result.detected_courses == ("CS F213",)
    assert result.total == 7
    assert all(hit.score == 0.0 for hit in result.hits)


def test_detection_can_be_switched_off(aliased: Catalog) -> None:
    result = aliased.search("m3 midsem", detect_courses=False)
    assert result.detected_courses == ()
    assert {hit.doc.course_code for hit in result.hits} >= {"CS F213", "MATH F211"}


def test_explicit_course_filter_beats_detection(aliased: Catalog) -> None:
    result = aliased.search("m3 midsem", SearchFilters(courses=frozenset({"CS F213"})))
    assert result.detected_courses == ()
    assert {hit.doc.course_code for hit in result.hits} == {"CS F213"}


def test_course_codes_work_without_aliases() -> None:
    catalog = Catalog(sample_views())
    for text in ("cs f213 compre", "CSF213 compre", "CS-F213 compre"):
        result = catalog.search(text)
        assert result.detected_courses == ("CS F213",), text
        assert names(result) == ["OOP Compre 2023.pdf"]


def test_multi_word_nickname_is_matched_as_one() -> None:
    logic = make_view("LCS Midsem.pdf", course_code="CS F214", course_name="Logic in CS")
    catalog = Catalog([logic], course_aliases=ALIASES)
    codes, rest = catalog.interpret("logic in cs midsem")
    assert codes == ("CS F214",)
    assert rest == "midsem"


def test_alias_for_course_without_documents_is_ignored(aliased: Catalog) -> None:
    result = aliased.search("ghost midsem")
    assert result.detected_courses == ()
    assert result.total > 0


def test_two_courses_in_one_query(aliased: Catalog) -> None:
    result = aliased.search("oop m3 midsem")
    assert set(result.detected_courses) == {"CS F213", "MATH F211"}


def test_course_label_helper(aliased: Catalog) -> None:
    assert aliased.course_label("CS F213") == "CS F213 · Object Oriented Programming"


def test_nickname_of_an_already_selected_course_is_dropped_from_the_text(aliased: Catalog) -> None:
    chosen = SearchFilters(courses=frozenset({"CS F213"}))
    with_nickname = aliased.search("oop midsem", chosen)
    without = aliased.search("midsem", chosen)
    assert names(with_nickname) == names(without)
    assert with_nickname.detected_courses == ()
