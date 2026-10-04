from unidex.ingestion.summary import summarize
from unidex.models.raw_file import RawFile


def make(path: str, name: str, extension: str, indexable: bool) -> RawFile:
    return RawFile(
        drive_file_id=name,
        path=path,
        name=name,
        extension=extension,
        mime_type="x",
        modified_on=None,
        url="u",
        is_indexable=indexable,
    )


def test_summarize_counts_by_folder_and_extension() -> None:
    files = [
        make("/2-1 CDCs/M3/Sem 1/Evals", "a.pdf", "pdf", True),
        make("/2-1 CDCs/M3/Sem 2", "b.pdf", "pdf", True),
        make("/2-1 CDCs/DD/Labs", "c.v", "v", False),
    ]
    summary = summarize(files)
    assert summary.total == 3
    assert summary.indexable == 2
    assert summary.by_course_folder == {"2-1 CDCs/M3": 2, "2-1 CDCs/DD": 1}
    assert summary.by_extension == {"pdf": 2, "v": 1}


def test_summarize_empty() -> None:
    summary = summarize([])
    assert (summary.total, summary.indexable) == (0, 0)
