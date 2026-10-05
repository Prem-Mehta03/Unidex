from pathlib import Path

import pytest

from tests.unit._catalog_data import ALIASES, chat_views
from unidex.chat.evaluation import evaluate, load_cases
from unidex.chat.service import ChatService
from unidex.exceptions import IngestionError
from unidex.search.catalog import Catalog

HEADER = (
    "message,previous_message,kind,courses,exam_types,materials,"
    "years,recent_years,topics,quiz_number,note\n"
)
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def service() -> ChatService:
    return ChatService(Catalog(chat_views(), course_aliases=ALIASES))


def write(tmp_path: Path, body: str, header: str = HEADER) -> Path:
    path = tmp_path / "cases.csv"
    path.write_text(header + body, encoding="utf-8")
    return path


def test_shipped_message_set_loads_and_is_big_enough() -> None:
    cases = load_cases(REPO / "eval" / "chat_messages.csv")
    assert len(cases) >= 30
    assert {c.kind for c in cases} >= {"confirm", "clarify", "smalltalk"}
    assert any(c.previous_message for c in cases)


def test_scores_a_correct_and_a_wrong_case(tmp_path: Path, service: ChatService) -> None:
    path = write(
        tmp_path,
        "OOP midsem papers,,confirm,CS F213,midsem,papers,,,,,\n"
        "OOP midsem papers,,confirm,CS F215,midsem,papers,,,,,wrong course on purpose\n",
    )
    report = evaluate(load_cases(path), service.reply)
    assert report.total == 2
    assert report.forms_correct == 1
    assert report.field_correct("courses") == 1
    assert report.field_correct("exam_types") == 2
    assert report.outcomes[1].wrong_fields == ["courses"]


def test_clarification_counts(tmp_path: Path, service: ChatService) -> None:
    path = write(tmp_path, "midsem papers,,clarify,,midsem,papers,,,,,\n")
    report = evaluate(load_cases(path), service.reply)
    assert report.clarifications == (1, 1)
    assert report.kinds_correct == 1


def test_default_and_all_materials(tmp_path: Path, service: ChatService) -> None:
    path = write(
        tmp_path,
        "oop,,confirm,CS F213,,default,,,,,\neverything for oop,,confirm,CS F213,,all,,,,,\n",
    )
    report = evaluate(load_cases(path), service.reply)
    assert report.forms_correct == 2


def test_multi_turn_cases_use_the_previous_message(tmp_path: Path, service: ChatService) -> None:
    path = write(
        tmp_path,
        "show only 2022 solutions,oop midsem papers,confirm,CS F213,midsem,solutions,2022,,,,\n",
    )
    assert evaluate(load_cases(path), service.reply).forms_correct == 1


def test_smalltalk_has_no_form_to_compare(tmp_path: Path, service: ChatService) -> None:
    report = evaluate(load_cases(write(tmp_path, "hello,,smalltalk,,,,,,,,\n")), service.reply)
    assert report.forms_correct == 1
    assert report.kinds_correct == 1


@pytest.mark.parametrize(
    "body",
    [
        "x,,bogus,,,,,,,,\n",
        "x,,confirm,,,,abc,,,,\n",
        "x,,confirm,,,,,notanumber,,,\n",
    ],
)
def test_bad_rows_are_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(IngestionError):
        load_cases(write(tmp_path, body))


def test_missing_column_and_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="missing column"):
        load_cases(write(tmp_path, "x,confirm\n", header="message,kind\n"))
    with pytest.raises(IngestionError, match="Cannot read"):
        load_cases(tmp_path / "nope.csv")
