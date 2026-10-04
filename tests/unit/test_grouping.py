from tests.unit._catalog_data import make_view, sample_views
from unidex.search.catalog import Catalog, Hit
from unidex.search.grouping import group_into_stacks


def hit(view, score: float = 1.0) -> Hit:
    return Hit(doc=view, score=score)


def test_papers_and_solutions_of_one_exam_share_a_stack() -> None:
    stacks = group_into_stacks(
        [
            hit(make_view("Paper.pdf")),
            hit(make_view("Solution.pdf", doc_type="solution", has_solution=True)),
        ]
    )
    assert len(stacks) == 1
    assert stacks[0].title == "Object Oriented Programming · Midsem papers"
    assert stacks[0].solution_count == 1


def test_different_exams_or_courses_get_different_stacks() -> None:
    stacks = group_into_stacks(
        [
            hit(make_view("A.pdf")),
            hit(make_view("B.pdf", exam_type="compre")),
            hit(make_view("C.pdf", course_code="CS F215", course_name="Digital Design")),
            hit(make_view("D.pdf", doc_type="slides", exam_type="none")),
        ]
    )
    assert {s.title for s in stacks} == {
        "Object Oriented Programming · Midsem papers",
        "Object Oriented Programming · Compre papers",
        "Digital Design · Midsem papers",
        "Object Oriented Programming · Lecture slides",
    }


def test_stacks_are_ordered_by_best_score() -> None:
    stacks = group_into_stacks(
        [
            hit(make_view("Low.pdf", doc_type="notes", exam_type="none"), 1.0),
            hit(make_view("High.pdf"), 5.0),
            hit(make_view("Mid.pdf", exam_type="compre"), 3.0),
        ]
    )
    assert [s.best_score for s in stacks] == [5.0, 3.0, 1.0]


def test_papers_read_newest_first_other_material_best_first() -> None:
    papers = group_into_stacks(
        [hit(make_view("Old.pdf", year=2019), 9.0), hit(make_view("New.pdf", year=2024), 1.0)]
    )
    assert [h.doc.name for h in papers[0].hits] == ["New.pdf", "Old.pdf"]
    notes = group_into_stacks(
        [
            hit(make_view("Old.pdf", doc_type="notes", exam_type="none", year=2019), 9.0),
            hit(make_view("New.pdf", doc_type="notes", exam_type="none", year=2024), 1.0),
        ]
    )
    assert [h.doc.name for h in notes[0].hits] == ["Old.pdf", "New.pdf"]


def test_year_span_formats() -> None:
    stacks = group_into_stacks(
        [hit(make_view("A.pdf", year=2019)), hit(make_view("B.pdf", year=2023))]
    )
    assert stacks[0].year_span == "2019-20 to 2023-24"
    single = group_into_stacks([hit(make_view("A.pdf", year=2023))])
    assert single[0].year_span == "2023-24"
    unknown = group_into_stacks([hit(make_view("A.pdf", year=None))])
    assert unknown[0].year_span == ""


def test_no_course_files_go_to_other_files() -> None:
    stacks = group_into_stacks(
        [
            hit(
                make_view(
                    "X.pdf", course_code="", course_name="", doc_type="other", exam_type="none"
                )
            )
        ]
    )
    assert stacks[0].title == "Other files"


def test_unknown_exam_type_still_forms_a_papers_stack() -> None:
    stacks = group_into_stacks([hit(make_view("X.pdf", exam_type="unknown"))])
    assert stacks[0].title.endswith("Exam papers")


def test_empty_input() -> None:
    assert group_into_stacks([]) == []


def test_grouping_real_search_keeps_every_hit() -> None:
    result = Catalog(sample_views()).search("midsem")
    stacks = group_into_stacks(result.hits)
    assert sum(len(s.hits) for s in stacks) == len(result.hits)


def test_filter_only_ties_put_papers_first() -> None:
    stacks = group_into_stacks(
        [
            hit(make_view("S.pdf", doc_type="slides", exam_type="none"), 0.0),
            hit(make_view("P.pdf"), 0.0),
        ]
    )
    assert stacks[0].kind == "papers"


def test_papers_with_no_known_exam_share_one_stack() -> None:
    stacks = group_into_stacks(
        [hit(make_view("A.pdf", exam_type="unknown")), hit(make_view("B.pdf", exam_type="none"))]
    )
    assert len(stacks) == 1
    assert stacks[0].title.endswith("Exam papers")
