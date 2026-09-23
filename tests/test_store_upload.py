import io

import pytest

from exceptions.validation import EmptyFileError, UnsupportedFileTypeError, UploadTooLargeError
from utils.store_upload import store_upload, store_uploads, validate_upload


class FakeUpload:
    """
    Stands in for Starlette's UploadFile.

    The real one streams, and so does this: `read(size)` hands back one chunk
    at a time, which is what the size checks are counting.
    """

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._stream = io.BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    async def seek(self, offset: int) -> None:
        self._stream.seek(offset)


def upload(content: bytes, filename: str = "report.pdf") -> FakeUpload:
    return FakeUpload(filename, content)


def test_validate_accepts_a_pdf():
    validate_upload("report.pdf", b"%PDF-1.4")


def test_validate_is_case_insensitive():
    validate_upload("REPORT.PDF", b"%PDF-1.4")


def test_validate_rejects_a_non_pdf():
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload("notes.txt", b"hello")


def test_validate_rejects_a_missing_filename():
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload(None, b"%PDF-1.4")


def test_validate_rejects_empty_bytes():
    with pytest.raises(EmptyFileError):
        validate_upload("report.pdf", b"")


async def test_the_staged_copy_is_removed_once_it_is_in_the_bucket(s3, settings, pdf_bytes):
    """It was staged so the upload could stream to disk, not to be kept."""
    stored = await store_upload(upload(pdf_bytes), settings)

    assert stored.local_pdf == ""
    assert list(settings.temp_pdf_path.iterdir()) == []


async def test_store_uploads_to_the_pdf_bucket(s3, settings, pdf_bytes):
    stored = await store_upload(upload(pdf_bytes), settings)

    assert s3.objects[(settings.s3_pdf_bucket, stored.pdf_key)] == pdf_bytes


async def test_store_does_not_touch_the_markdown_bucket(s3, settings, pdf_bytes):
    """Only the workflow writes markdown."""
    await store_upload(upload(pdf_bytes), settings)

    assert not any(bucket == settings.s3_parsed_mds for bucket, _ in s3.objects)


async def test_the_task_id_ties_both_keys_together(s3, settings, pdf_bytes):
    stored = await store_upload(upload(pdf_bytes), settings)

    assert stored.task_id in stored.pdf_key
    assert stored.task_id in stored.md_key


async def test_two_uploads_do_not_collide(s3, settings, pdf_bytes):
    first = await store_upload(upload(pdf_bytes), settings)
    second = await store_upload(upload(pdf_bytes), settings)

    assert first.task_id != second.task_id
    assert first.pdf_key != second.pdf_key


# --- the limits ----------------------------------------------------------


def test_validate_rejects_a_pdf_that_is_not_one():
    """The extension is a claim; the first bytes are evidence."""
    with pytest.raises(UnsupportedFileTypeError, match="does not contain a PDF"):
        validate_upload("report.pdf", b"PK\x03\x04 this is a zip")


async def test_a_file_past_the_limit_is_refused(s3, settings, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)

    with pytest.raises(UploadTooLargeError, match="larger than"):
        await store_upload(upload(b"%PDF-1.4" + b"x" * 4096), settings)


async def test_a_refused_file_leaves_nothing_behind(s3, settings, monkeypatch):
    """A partial copy on disk would outlive the request that was rejected."""
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)

    with pytest.raises(UploadTooLargeError):
        await store_upload(upload(b"%PDF-1.4" + b"x" * 4096), settings)

    assert list(settings.temp_pdf_path.iterdir()) == []
    assert not s3.objects


async def test_nothing_reaches_the_bucket_when_the_upload_is_refused(s3, settings):
    with pytest.raises(UnsupportedFileTypeError):
        await store_upload(upload(b"not a pdf at all", "notes.pdf"), settings)

    assert not s3.objects


async def test_the_request_has_an_allowance_of_its_own(s3, settings, monkeypatch, pdf_bytes):
    """Twenty files each just under the per-file limit must not add up."""
    monkeypatch.setattr(settings, "max_request_bytes", len(pdf_bytes) + 10)

    with pytest.raises(UploadTooLargeError, match="in total"):
        await store_uploads([upload(pdf_bytes, "one.pdf"), upload(pdf_bytes, "two.pdf")], settings)


async def test_a_large_upload_is_never_held_in_memory(s3, settings, pdf_bytes, monkeypatch):
    """
    The point of the change: the file goes to disk a chunk at a time.

    A `read()` with no size argument would hand back the whole file, so the
    test asserts every read asks for a bounded chunk.
    """
    asked = []

    class Watched(FakeUpload):
        async def read(self, size: int = -1) -> bytes:
            asked.append(size)
            return await super().read(size)

    await store_upload(Watched("report.pdf", pdf_bytes), settings)

    assert asked and all(size > 0 for size in asked)
