import pytest
from fastapi.testclient import TestClient

from tests.unit._catalog_data import make_view, sample_views
from unidex.api.app import create_app
from unidex.api.presenter import exam_label, safe_url
from unidex.search.catalog import Catalog


@pytest.fixture
def client():
    with TestClient(create_app(catalog=Catalog(sample_views()), web_dir=None)) as test_client:
        yield test_client


def test_health_reports_document_count(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok", "documents": 10}


def test_search_returns_stacks_and_facets(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "midsem"}).json()
    assert body["total_files"] > 0
    assert body["total_stacks"] == len(body["stacks"])
    titles = {s["title"] for s in body["stacks"]}
    assert "Object Oriented Programming · Midsem papers" in titles
    assert set(body["facets"]) == {"course", "doc_type", "exam_type", "year"}
    card = body["stacks"][0]["files"][0]
    assert card["url"].startswith("https://")
    assert card["why"].startswith("Matches")


def test_course_filter_applies(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "midsem", "course": "CS F213"}).json()
    codes = {f["course_code"] for s in body["stacks"] for f in s["files"]}
    assert codes == {"CS F213"}


def test_repeated_parameters_mean_any_of(client: TestClient) -> None:
    params = [("course", "CS F213"), ("exam_type", "midsem"), ("exam_type", "compre")]
    body = client.get("/api/search", params=params).json()
    exams = {f["exam_type"] for s in body["stacks"] for f in s["files"]}
    assert exams == {"midsem", "compre"}


def test_filter_only_search_works_without_text(client: TestClient) -> None:
    body = client.get("/api/search", params={"year": 2022, "course": "CS F213"}).json()
    assert body["total_files"] == 1
    assert body["stacks"][0]["files"][0]["why"] == "Matches your filters."


def test_empty_search_returns_no_stacks(client: TestClient) -> None:
    body = client.get("/api/search").json()
    assert body["stacks"] == []
    assert body["facets"]["course"]


def test_stack_paging(client: TestClient) -> None:
    first = client.get("/api/search", params={"q": "pdf", "stack_limit": 1}).json()
    assert len(first["stacks"]) == 1
    assert first["has_more"] is True
    second = client.get(
        "/api/search", params={"q": "pdf", "stack_limit": 1, "stack_offset": 1}
    ).json()
    assert second["stacks"][0]["key"] != first["stacks"][0]["key"]
    last = client.get("/api/search", params={"q": "pdf", "stack_offset": 0}).json()
    assert last["has_more"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"doc_type": "bogus"},
        {"exam_type": "finals"},
        {"year": 1999},
        {"year": "abc"},
        {"q": "x" * 201},
        {"stack_limit": 0},
        {"stack_limit": 500},
        {"stack_offset": -1},
    ],
)
def test_bad_parameters_are_rejected(client: TestClient, params: dict[str, object]) -> None:
    assert client.get("/api/search", params=params).status_code == 422


def test_suggest(client: TestClient) -> None:
    assert "laplace" in client.get("/api/suggest", params={"prefix": "lap"}).json()["suggestions"]
    assert client.get("/api/suggest").json() == {"suggestions": []}


def test_facets_endpoint_lists_whole_catalog(client: TestClient) -> None:
    body = client.get("/api/facets").json()
    assert {o["value"] for o in body["course"]} == {"CS F213", "MATH F211"}


def test_security_headers(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    docs = client.get("/docs")
    assert "content-security-policy" not in docs.headers


def test_unsafe_links_are_blanked() -> None:
    assert safe_url("javascript:alert(1)") == ""
    assert safe_url("data:text/html,x") == ""
    assert safe_url("HTTPS://drive.google.com/x") == "HTTPS://drive.google.com/x"


def test_unsafe_link_never_reaches_the_browser() -> None:
    bad = Catalog([make_view("Evil.pdf", url="javascript:alert(1)")])
    with TestClient(create_app(catalog=bad, web_dir=None)) as client:
        card = client.get("/api/search", params={"q": "evil"}).json()["stacks"][0]["files"][0]
    assert card["url"] == ""


def test_reviewed_and_uncertain_flags(client: TestClient) -> None:
    body = client.get("/api/search", params={"q": "guess"}).json()
    assert body["stacks"][0]["files"][0]["uncertain"] is True
    body = client.get("/api/search", params={"q": "checked"}).json()
    card = body["stacks"][0]["files"][0]
    assert card["reviewed"] is True
    assert card["uncertain"] is False
    assert card["exam_label"] == "Quiz 2"


@pytest.mark.parametrize(
    ("exam_type", "number", "makeup", "expected"),
    [
        ("midsem", None, False, "Midsem"),
        ("quiz", 2, False, "Quiz 2"),
        ("test", 1, True, "Test 1 (make-up)"),
        ("midsem", 3, False, "Midsem"),
        ("none", None, False, ""),
        ("unknown", None, False, ""),
    ],
)
def test_exam_label(exam_type: str, number: int | None, makeup: bool, expected: str) -> None:
    assert exam_label(exam_type, number, makeup) == expected


def test_static_front_end_is_served(tmp_path) -> None:
    (tmp_path / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    app = create_app(catalog=Catalog([]), web_dir=tmp_path)
    with TestClient(app) as client:
        assert "<h1>hi</h1>" in client.get("/").text
        assert client.get("/api/health").json()["documents"] == 0


def test_search_reports_detected_course_and_can_undo_it() -> None:
    catalog = Catalog(sample_views(), course_aliases={"oop": "CS F213", "m3": "MATH F211"})
    with TestClient(create_app(catalog=catalog, web_dir=None)) as client:
        body = client.get("/api/search", params={"q": "oop midsem"}).json()
        assert body["detected_courses"] == [
            {"code": "CS F213", "label": "CS F213 · Object Oriented Programming"}
        ]
        assert {f["course_code"] for s in body["stacks"] for f in s["files"]} == {"CS F213"}
        plain = client.get("/api/search", params={"q": "m3 midsem", "detect": "false"}).json()
        assert plain["detected_courses"] == []
        explicit = client.get("/api/search", params={"q": "m3 midsem", "course": "CS F213"}).json()
        assert explicit["detected_courses"] == []
