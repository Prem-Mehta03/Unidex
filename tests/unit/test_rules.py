import pytest

from unidex.extraction.rules import RuleExtractor, year_from_name
from unidex.models.enums import DocType, ExamType, SyllabusScope
from unidex.models.metadata import ExtractedMetadata
from unidex.models.raw_file import RawFile
from unidex.normalize import normalize_alias

COURSES = {"oop": 1, "lcs": 2, "disco": 3, "m3": 4, "dd": 5}
GOOGLE_DOC = "application/vnd.google-apps.document"


def resolve(text: str) -> int | None:
    return COURSES.get(normalize_alias(text))


def extract(path: str, name: str, mime: str = "application/pdf") -> ExtractedMetadata:
    extension = "" if mime == GOOGLE_DOC else name.rsplit(".", 1)[-1].lower()
    file = RawFile("id", path, name, extension, mime, None, "https://drive.google.com/x", True)
    return RuleExtractor(resolve).extract(file)


# ------------------------------------------------------------------ exams and numbers


def test_quiz_in_quizzes_folder() -> None:
    meta = extract("/OOP/25-26 Sem 1 (Neena)/Quizzes", "Quiz 2.pdf")
    assert (meta.doc_type, meta.exam_type, meta.exam_number) == (DocType.PYQ, ExamType.QUIZ, 2)
    assert (meta.academic_year, meta.semester, meta.instructor) == (2025, 1, "Neena")
    assert meta.confidence >= 0.9


def test_makeup_quiz_is_flagged() -> None:
    meta = extract("/DISCO/Sem 1 25-26 (S Gupta)/Quizzes", "Quiz-2 Makeup.pdf")
    assert (meta.exam_type, meta.exam_number, meta.is_makeup) == (ExamType.QUIZ, 2, True)


def test_midsem_solution_has_solution_flag() -> None:
    meta = extract("/M3/Sem 1 24-25 (X)/Midsem", "Midsem Solution.pdf")
    assert (meta.doc_type, meta.exam_type, meta.has_solution) == (
        DocType.SOLUTION,
        ExamType.MIDSEM,
        True,
    )


def test_old_test_papers_get_their_own_exam_type() -> None:
    meta = extract("/LCS/Sem 1 15-16", "Test 1 2015.pdf")
    assert (meta.doc_type, meta.exam_type, meta.exam_number) == (DocType.PYQ, ExamType.TEST, 1)
    assert meta.academic_year == 2015
    assert meta.confidence >= 0.9


def test_short_test_names_and_answer_files() -> None:
    meta = extract("/DISCO/20-21 Sem 1 (CS_23 - Anup Mathew)", "T3_ans.pdf")
    assert (meta.doc_type, meta.exam_type, meta.exam_number) == (DocType.SOLUTION, ExamType.TEST, 3)
    assert meta.instructor == "Anup Mathew"


def test_key_after_an_exam_word_means_answer_key() -> None:
    meta = extract("/M3/Sem 1 25-26 (X)/Evals", "Quiz 1 Key.pdf")
    assert (meta.doc_type, meta.exam_type, meta.exam_number) == (DocType.SOLUTION, ExamType.QUIZ, 1)
    assert meta.has_solution


def test_quiz_number_from_q_suffix_and_from_folder() -> None:
    assert extract("/M3/Sem 1 24-25 (X)/Quizzes", "Answer_Key_Q2.pdf").exam_number == 2
    assert extract("/LCS/Sem 1 25-26 (X)/Prolog Labs/Lab Test 1", "Prolog 1.pdf").exam_number == 1
    assert extract("/OOP/22-23 Sem 1 (X)/Lab Quiz", "LQ9.pdf").exam_number == 9


def test_lab_exam_folders() -> None:
    meta = extract("/DD/Sem 1 25-26 (X)/Lab Compre", "Paper.pdf")
    assert (meta.doc_type, meta.exam_type) == (DocType.PYQ, ExamType.LAB_COMPRE)
    meta = extract("/OOP/22-23 Sem 1 (X)/Lab Quiz", "LQ3.pdf")
    assert (meta.doc_type, meta.exam_type) == (DocType.PYQ, ExamType.LAB_QUIZ)


def test_name_with_two_exams_is_settled_by_the_folder() -> None:
    meta = extract("/OOP/25-26 Sem 1 (X)/Midsem /Midsem", "Midsem_Quiz.pdf")
    assert meta.exam_type is ExamType.MIDSEM
    assert meta.confidence >= 0.8


def test_name_with_two_exams_and_no_folder_goes_to_review() -> None:
    meta = extract("/OOP/24-25 Sem 1 (X)", "Compre Quiz 1A.pdf")
    assert (meta.exam_type, meta.exam_number) == (ExamType.QUIZ, 1)
    assert meta.confidence < 0.7
    assert "name mentions more than one exam" in meta.review_reasons


def test_re_exam_counts_as_makeup() -> None:
    meta = extract("/OOP/25-26 Sem 1 (X)/Midsem /Re-Midsem", "ReMidsem_PQ1.pdf")
    assert (meta.exam_type, meta.is_makeup) == (ExamType.MIDSEM, True)


# ------------------------------------------------------------------------ doc types


def test_topic_called_solution_in_a_slides_folder_is_not_an_answer_file() -> None:
    meta = extract("/M3/Sem 1 25-26 (X)/Slides/Mayank (Pre Midsem)", "Series Solution of ODE.pdf")
    assert (meta.doc_type, meta.exam_type, meta.has_solution) == (
        DocType.SLIDES,
        ExamType.NONE,
        False,
    )
    assert meta.syllabus_scope is SyllabusScope.PRE_MIDSEM


def test_slides_inside_an_exam_named_folder_are_still_slides() -> None:
    meta = extract("/LCS/Sem 1 21-22 (X)/Quiz/Slides and Notes", "CSF214Slides21.pdf")
    assert (meta.doc_type, meta.exam_type) == (DocType.SLIDES, ExamType.NONE)


def test_solution_folder_inside_labs_is_lab_material_with_solution() -> None:
    meta = extract("/OOP/23-24 Sem 1 (X)/Labs/Lab 12 (UML)/Solution", "x.png", "image/png")
    assert (meta.doc_type, meta.has_solution) == (DocType.LAB, True)


def test_tutorials_with_solutions_folder() -> None:
    meta = extract("/M3/Sem 1 25-26 (X)/Tutorials (With Solutions)", "M3 Tutorial 11.pdf")
    assert (meta.doc_type, meta.exam_type, meta.has_solution) == (
        DocType.TUTORIAL,
        ExamType.NONE,
        True,
    )


def test_homework_solutions_are_solutions_without_an_exam() -> None:
    meta = extract("/DISCO/21-22 Sem 1(CS_24 - A)/HW Soln", "Homework #3 - Solutions.pdf")
    assert (meta.doc_type, meta.exam_type) == (DocType.SOLUTION, ExamType.NONE)
    assert meta.confidence >= 0.7


def test_google_doc_av_details_is_grade_statistics() -> None:
    meta = extract("/M3/Sem 1 25-26 (A Sharma)", "Av Details", GOOGLE_DOC)
    assert (meta.doc_type, meta.exam_type, meta.academic_year) == (
        DocType.GRADE_STATS,
        ExamType.NONE,
        2025,
    )


def test_marks_distribution_folder_keeps_the_exam() -> None:
    meta = extract(
        "/LCS/Sem 1 25-26 (B)/Insights (Marks Distribution Analysis)", "Online Quiz 2.pdf"
    )
    assert (meta.doc_type, meta.exam_type, meta.exam_number) == (
        DocType.GRADE_STATS,
        ExamType.QUIZ,
        2,
    )


def test_formula_sheet_is_a_cheatsheet_not_a_paper() -> None:
    meta = extract("/M3/Sem 1 25-26 (X)/Evals", "Quiz 1 Formula Sheet - Krish.pdf")
    assert (meta.doc_type, meta.exam_type) == (DocType.CHEATSHEET, ExamType.QUIZ)


@pytest.mark.parametrize(
    ("folder", "name", "expected"),
    [
        ("Slides", "Lect5.pdf", DocType.SLIDES),
        ("Lecture Notes", "Lec4.pdf", DocType.NOTES),
        ("Topicwise Guides", "Fourier Series.pdf", DocType.NOTES),
        ("Textbooks", "Prolog.pdf", DocType.TEXTBOOK),
        ("Problem Sheets", "Problem Sheet 1.pdf", DocType.TUTORIAL),
        ("Tutorials", "Tutorial-4.pdf", DocType.TUTORIAL),
        ("Homework", "Homework-1.pdf", DocType.ASSIGNMENT),
        ("Labs", "Lab 3.docx", DocType.LAB),
        ("", "Dynamic Handout Final.pdf", DocType.HANDOUT),
        ("Cheatsheets", "CompreCheatsheet1.pdf", DocType.CHEATSHEET),
    ],
)
def test_common_folder_and_name_patterns(folder: str, name: str, expected: DocType) -> None:
    meta = extract(f"/DD/Sem 1 25-26 (X)/{folder}".rstrip("/"), name)
    assert meta.doc_type is expected
    assert meta.confidence >= 0.7


def test_unmatched_file_is_unknown_and_wants_the_language_model() -> None:
    meta = extract("/LCS/Sem 1 22-23 (X)", "Prolog.pdf")
    assert meta.doc_type is DocType.UNKNOWN
    assert meta.needs_llm
    assert "document type unknown" in meta.review_reasons


def test_unknown_course_forces_review() -> None:
    meta = extract("/Biology/Sem 1 25-26 (X)/Quizzes", "Quiz 1.pdf")
    assert meta.course_id is None
    assert meta.confidence == 0
    assert "course not recognised" in meta.review_reasons


# ------------------------------------------------------------------------------ years


def test_lecture_range_is_not_an_academic_year() -> None:
    meta = extract("/M3/Sem 1 24-25 (X)/Slides (J.K. Sahoo)", "Lecture-16-17.pdf")
    assert meta.academic_year == 2024
    assert meta.confidence >= 0.7
    assert not any("differs" in note for note in meta.notes)


def test_past_material_without_a_year_in_the_name_has_no_year() -> None:
    meta = extract("/LCS/Sem 1 21-22 (X)/Past Material/Papers", "Quiz1.pdf")
    assert meta.academic_year is None
    assert "academic year unknown" in meta.review_reasons


def test_past_material_takes_the_year_from_the_file_name() -> None:
    meta = extract("/LCS/Sem 1 21-22 (X)/Past Material/Papers", "Quiz 1 2018.pdf")
    assert meta.academic_year == 2018


def test_year_in_name_that_disagrees_with_folder_is_flagged() -> None:
    meta = extract("/OOP/22-23 Sem 1 (X)", "Handout 19-20.pdf")
    assert meta.academic_year == 2022
    assert meta.confidence < 0.7


def test_files_without_a_semester_folder_use_the_name() -> None:
    meta = extract("/OOP", "CS F213 15-16.pdf")
    assert meta.academic_year == 2015
    assert meta.semester is None


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("CS F213 15-16", (2015, True)),
        ("LiCS1617SemICompreSoln", (2016, True)),
        ("csf222disco2022 Midsem", (2022, False)),
        ("CS F213 Handout_1 2022 23", (2022, True)),
        ("Quiz 1 2018", (2018, False)),
        ("Lecture-16-17", (None, False)),
        ("Tutorial 3-4", (None, False)),
        ("IMG-20181129-WA0008", (None, False)),
        ("2020A7PS0114G", (None, False)),
        ("Midsem", (None, False)),
    ],
)
def test_year_from_name(stem: str, expected: tuple[int | None, bool]) -> None:
    assert year_from_name(stem) == expected


# -------------------------------------------------------------------------- privacy


def test_student_id_file_names_are_flagged_for_review() -> None:
    meta = extract("/LCS/Sem 1 21-22 (X)/Evals/Compre", "2020A7PS0114G.pdf")
    assert meta.confidence < 0.7
    assert any("student id" in reason for reason in meta.review_reasons)


def test_title_is_the_file_name_without_extension() -> None:
    assert extract("/OOP/25-26 Sem 1 (X)", "Midsem_Odd_PQ.pdf").title == "Midsem Odd PQ"
    assert extract("/M3/Sem 1 25-26 (X)", "Av Details", GOOGLE_DOC).title == "Av Details"


def test_solution_of_a_topic_is_not_an_answer_file_even_outside_slides_folders() -> None:
    meta = extract("/M3/Sem 1 25-26 (X)", "Series Solution of ODE.pdf")
    assert meta.doc_type is not DocType.SOLUTION
    assert not meta.has_solution
