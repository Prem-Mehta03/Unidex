import re
from datetime import date

import httpx2 as httpx
import pytest

from unidex.exceptions import IngestionError
from unidex.ingestion.drive_source import (
    FOLDER_MIME,
    SHORTCUT_MIME,
    DriveFileSource,
    folder_id_from,
)

PDF = "application/pdf"


def folder(item_id: str, name: str) -> dict:
    return {"id": item_id, "name": name, "mimeType": FOLDER_MIME}


def pdf(item_id: str, name: str, modified: str = "2025-10-12T08:30:00.000Z") -> dict:
    return {
        "id": item_id,
        "name": name,
        "mimeType": PDF,
        "modifiedTime": modified,
        "webViewLink": f"https://drive.google.com/file/d/{item_id}/view?usp=drivesdk",
    }


class FakeDrive:
    """A tiny pretend Drive: folder id -> items, served in pages of two."""

    def __init__(self, tree: dict[str, list[dict]]) -> None:
        self.tree = tree
        self.calls: list[dict[str, str]] = []
        self.fail_next: list[httpx.Response] = []
        self.auth_headers: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        self.auth_headers.append(request.headers["authorization"])
        if self.fail_next:
            return self.fail_next.pop(0)
        match = re.match(r"'([^']+)' in parents and trashed = false", params["q"])
        assert match, params["q"]
        items = self.tree.get(match.group(1))
        if items is None:
            return httpx.Response(404, json={"error": {"errors": [{"reason": "notFound"}]}})
        start = int(params.get("pageToken", "0"))
        page = items[start : start + 2]
        body: dict = {"files": page}
        if start + 2 < len(items):
            body["nextPageToken"] = str(start + 2)
        return httpx.Response(200, json=body)


def source_for(drive: FakeDrive, root: str = "ROOT") -> tuple[DriveFileSource, list[float]]:
    pauses: list[float] = []
    source = DriveFileSource(
        lambda: "token-1",
        root,
        http=httpx.Client(transport=httpx.MockTransport(drive)),
        sleep=pauses.append,
    )
    return source, pauses


def sample_tree() -> dict[str, list[dict]]:
    return {
        "ROOT": [folder("CDC", "2-1 CDCs"), pdf("TOP", "Readme.pdf")],
        "CDC": [folder("OOP", "OOP"), pdf("B1", "b.pdf"), pdf("B2", "a.pdf")],
        "OOP": [
            folder("MID", "Midsem "),
            pdf("P1", "Midsem 25-26.pdf"),
            {"id": "X", "name": "main.java", "mimeType": "text/x-java"},
        ],
        "MID": [pdf("P2", "Q1.pdf", modified="")],
    }


def test_walks_every_folder_and_builds_csv_style_paths() -> None:
    drive = FakeDrive(sample_tree())
    source, _ = source_for(drive)
    files = {f.drive_file_id: f for f in source.fetch()}
    assert set(files) == {"TOP", "B1", "B2", "P1", "X", "P2"}
    assert files["TOP"].path == "/"
    assert files["B1"].path == "/2-1 CDCs"
    assert files["P1"].path == "/2-1 CDCs/OOP"
    assert files["P2"].path == "/2-1 CDCs/OOP/Midsem "
    assert files["P1"].name == "Midsem 25-26.pdf"


def test_file_fields_are_filled_in() -> None:
    source, _ = source_for(FakeDrive(sample_tree()))
    files = {f.drive_file_id: f for f in source.fetch()}
    p1 = files["P1"]
    assert p1.extension == "pdf"
    assert p1.mime_type == PDF
    assert p1.modified_on == date(2025, 10, 12)
    assert p1.url == "https://drive.google.com/file/d/P1/view?usp=drivesdk"
    assert p1.is_indexable
    assert files["P2"].modified_on is None
    assert files["X"].url == "https://drive.google.com/open?id=X"
    assert not files["X"].is_indexable


def test_pages_are_followed() -> None:
    drive = FakeDrive(sample_tree())
    source, _ = source_for(drive)
    assert len(list(source.fetch())) == 6
    cdc_calls = [c for c in drive.calls if "'CDC'" in c["q"]]
    assert len(cdc_calls) == 2  # three items, two per page
    assert all(c["supportsAllDrives"] == "true" for c in drive.calls)


def test_token_is_sent_on_every_call() -> None:
    drive = FakeDrive(sample_tree())
    source, _ = source_for(drive)
    list(source.fetch())
    assert set(drive.auth_headers) == {"Bearer token-1"}


def test_shortcut_to_folder_is_followed_and_loops_end() -> None:
    tree = {
        "ROOT": [
            {
                "id": "SC1",
                "name": "Linked",
                "mimeType": SHORTCUT_MIME,
                "shortcutDetails": {"targetId": "OTHER", "targetMimeType": FOLDER_MIME},
            },
        ],
        "OTHER": [
            pdf("F1", "x.pdf"),
            {
                "id": "SC2",
                "name": "Back",
                "mimeType": SHORTCUT_MIME,
                "shortcutDetails": {"targetId": "ROOT", "targetMimeType": FOLDER_MIME},
            },
        ],
    }
    source, _ = source_for(FakeDrive(tree))
    files = list(source.fetch())
    assert [(f.drive_file_id, f.path) for f in files] == [("F1", "/Linked")]


def test_shortcut_to_file_uses_the_target_and_a_generic_link() -> None:
    tree = {
        "ROOT": [
            {
                "id": "SC",
                "name": "Notes.pdf",
                "mimeType": SHORTCUT_MIME,
                "shortcutDetails": {"targetId": "REAL", "targetMimeType": PDF},
            },
            pdf("REAL", "Notes.pdf"),
        ]
    }
    source, _ = source_for(FakeDrive(tree))
    files = list(source.fetch())
    assert [f.drive_file_id for f in files] == ["REAL"]  # listed once, not twice


def test_broken_shortcut_and_nameless_items_are_skipped() -> None:
    tree = {
        "ROOT": [
            {"id": "SC", "name": "Dead", "mimeType": SHORTCUT_MIME},
            {"id": "", "name": "no id", "mimeType": PDF},
            {"id": "Z", "name": "  ", "mimeType": PDF},
            pdf("OK", "ok.pdf"),
        ]
    }
    source, _ = source_for(FakeDrive(tree))
    assert [f.drive_file_id for f in source.fetch()] == ["OK"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_temporary_errors_are_retried_with_growing_pauses(status: int) -> None:
    drive = FakeDrive({"ROOT": [pdf("A", "a.pdf")]})
    drive.fail_next = [httpx.Response(status), httpx.Response(status)]
    source, pauses = source_for(drive)
    assert [f.drive_file_id for f in source.fetch()] == ["A"]
    assert pauses == [1, 2]


def test_rate_limit_403_is_retried() -> None:
    drive = FakeDrive({"ROOT": [pdf("A", "a.pdf")]})
    drive.fail_next = [
        httpx.Response(403, json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}})
    ]
    source, pauses = source_for(drive)
    assert len(list(source.fetch())) == 1
    assert pauses == [1]


def test_giving_up_after_repeated_failures() -> None:
    drive = FakeDrive({"ROOT": []})
    drive.fail_next = [httpx.Response(503)] * 10
    source, pauses = source_for(drive)
    with pytest.raises(IngestionError, match="503"):
        list(source.fetch())
    assert len(pauses) == 4


def test_permission_403_is_not_retried() -> None:
    drive = FakeDrive({"ROOT": []})
    drive.fail_next = [
        httpx.Response(403, json={"error": {"errors": [{"reason": "insufficientPermissions"}]}})
    ]
    source, pauses = source_for(drive)
    with pytest.raises(IngestionError, match="insufficientPermissions"):
        list(source.fetch())
    assert pauses == []


def test_missing_root_gives_a_helpful_message() -> None:
    source, _ = source_for(FakeDrive({}), root="NOPE")
    with pytest.raises(IngestionError, match="not shared"):
        list(source.fetch())


def test_unauthorised_says_to_sign_in_again() -> None:
    drive = FakeDrive({"ROOT": []})
    drive.fail_next = [httpx.Response(401)]
    source, _ = source_for(drive)
    with pytest.raises(IngestionError, match="drive_login"):
        list(source.fetch())


def test_failure_in_a_subfolder_fails_the_whole_listing() -> None:
    tree = sample_tree()
    del tree["OOP"]  # a subfolder that suddenly answers 404
    source, _ = source_for(FakeDrive(tree))
    with pytest.raises(IngestionError):
        list(source.fetch())


def test_network_error_is_retried_then_reported() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    pauses: list[float] = []
    source = DriveFileSource(
        lambda: "t",
        "ROOT",
        http=httpx.Client(transport=httpx.MockTransport(boom)),
        sleep=pauses.append,
    )
    with pytest.raises(IngestionError, match="reach"):
        list(source.fetch())
    assert len(pauses) == 4


def test_unreadable_answer() -> None:
    source = DriveFileSource(
        lambda: "t",
        "ROOT",
        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<<"))),
        sleep=lambda _: None,
    )
    with pytest.raises(IngestionError, match="unreadable"):
        list(source.fetch())


class TestFolderId:
    def test_plain_id(self) -> None:
        assert folder_id_from(" 1AbC_-9 ") == "1AbC_-9"

    def test_link(self) -> None:
        link = "https://drive.google.com/drive/folders/1AbC_-9?usp=sharing"
        assert folder_id_from(link) == "1AbC_-9"

    @pytest.mark.parametrize("bad", ["", "a b", "x' or name contains 'y", "../etc"])
    def test_rejects_anything_that_could_change_the_query(self, bad: str) -> None:
        with pytest.raises(IngestionError):
            folder_id_from(bad)


def test_path_prefix_puts_the_course_folder_in_front() -> None:
    source = DriveFileSource(
        lambda: "t",
        "ROOT",
        http=httpx.Client(transport=httpx.MockTransport(FakeDrive(sample_tree()))),
        sleep=lambda _: None,
        path_prefix=" /M3/ ",
    )
    paths = {f.drive_file_id: f.path for f in source.fetch()}
    assert paths["TOP"] == "/M3"
    assert paths["B1"] == "/M3/2-1 CDCs"
    assert paths["P2"] == "/M3/2-1 CDCs/OOP/Midsem "
