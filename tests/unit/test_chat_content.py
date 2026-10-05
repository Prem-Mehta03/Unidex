"""Topic search inside papers: the planner with stored text."""

from tests.unit._catalog_data import ALIASES, chat_views
from unidex.chat.models import Interpretation, Material
from unidex.chat.planner import ChatPlanner
from unidex.search.catalog import Catalog

OOP = ("CS F213",)


def texts_for(views, mapping: dict[str, str]) -> dict[str, str]:
    by_name = {v.name: v.drive_file_id for v in views}
    return {by_name[name]: text for name, text in mapping.items()}


def planner_with(mapping: dict[str, str]) -> tuple[ChatPlanner, Catalog]:
    views = chat_views()
    catalog = Catalog(views, course_aliases=ALIASES, texts=texts_for(views, mapping))
    return ChatPlanner(catalog), catalog


def listing(result) -> dict[str, str]:
    return {h.doc.name: h.source for s in result.stacks for h in s.hits}


def interp(**kw) -> Interpretation:
    kw.setdefault("materials", (Material.PAPERS,))
    return Interpretation(courses=OOP, topics=("inheritance",), **kw)


def test_papers_are_matched_by_the_text_inside_them() -> None:
    planner, _ = planner_with(
        {
            "OOP Compre 2022.pdf": "Explain inheritance and method overriding in Java " * 3,
            "OOP Compre 2023.pdf": "Write a program using threads and locks for a bank " * 3,
        }
    )
    result = planner.run(interp(exam_types=("compre",)))
    found = listing(result)
    assert found["OOP Compre 2022.pdf"] == "content"
    assert "OOP Compre 2023.pdf" not in found  # readable and does not mention the topic
    assert any("reading the text inside" in n for n in result.notes)


def test_solutions_stay_with_a_matching_paper() -> None:
    planner, _ = planner_with(
        {
            "OOP Midsem 2022.pdf": "inheritance in classes " * 5,
            "OOP Midsem 2021.pdf": "interfaces and generics " * 5,
        }
    )
    found = listing(planner.run(interp(materials=(Material.PAPERS, Material.SOLUTIONS))))
    assert found["OOP Midsem 2022.pdf"] == "content"
    assert found["OOP Midsem 2022 Solutions.pdf"] == "paired"
    assert "OOP Midsem 2021.pdf" not in found


def test_unreadable_papers_are_listed_last_and_labelled() -> None:
    planner, _ = planner_with({"OOP Compre 2022.pdf": "inheritance rules " * 5})
    result = planner.run(interp(exam_types=("compre",)))
    found = listing(result)
    assert found["OOP Compre 2022.pdf"] == "content"
    assert found["OOP Compre 2023.pdf"] == "unreadable"
    assert any("could not be read" in n for n in result.notes)


def test_nothing_matches_says_so() -> None:
    planner, _ = planner_with({"OOP Compre 2022.pdf": "threads and locks " * 5})
    result = planner.run(interp(exam_types=("compre",)))
    assert any("mention your topics" in n for n in result.notes)
    assert listing(result)["OOP Compre 2023.pdf"] == "unreadable"


def test_no_stored_text_keeps_the_old_behaviour() -> None:
    planner, catalog = planner_with({})
    assert not catalog.has_content
    result = planner.run(interp(exam_types=("compre",)))
    assert len(listing(result)) == 2
    assert any("cannot be searched by topic" in n for n in result.notes)


def test_notes_and_slides_also_use_content() -> None:
    planner, _ = planner_with({"Polymorphism Lecture 4.pdf": "inheritance hierarchy " * 5})
    result = planner.run(
        Interpretation(courses=OOP, materials=(Material.SLIDES,), topics=("inheritance",))
    )
    found = listing(result)
    assert found["Inheritance Lecture 3.pdf"] == "name"
    assert found["Polymorphism Lecture 4.pdf"] == "content"
    assert any("text inside" in n for n in result.notes)
