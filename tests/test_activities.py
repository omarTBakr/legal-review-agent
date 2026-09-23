"""Each activity is exercised through Temporal's ActivityEnvironment, so the
@activity.defn wrapper is covered without needing a running Temporal server."""

import pytest
from temporalio.testing import ActivityEnvironment

from activities import ALL_ACTIVITIES, download_md, download_pdf, parse_pdf, upload_md, upload_pdf
from schemas.download_md import DownloadMdInput
from schemas.download_pdf import DownloadPdfInput
from schemas.parse_pdf import ParsePdfInput
from schemas.upload_md import UploadMdInput
from schemas.upload_pdf import UploadPdfInput


@pytest.fixture
def env():
    return ActivityEnvironment()


async def test_upload_pdf(env, s3, settings, pdf_bytes, tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(pdf_bytes)

    result = await env.run(upload_pdf, UploadPdfInput(task_id="t1", local_path=str(source), key="report.pdf"))

    assert result.bucket == settings.s3_pdf_bucket
    assert result.key == "report.pdf"
    assert s3.objects[(settings.s3_pdf_bucket, "report.pdf")] == pdf_bytes


async def test_download_pdf(env, s3, settings, pdf_bytes):
    s3.objects[(settings.s3_pdf_bucket, "report.pdf")] = pdf_bytes

    result = await env.run(download_pdf, DownloadPdfInput(task_id="t1", key="report.pdf"))

    assert result.bucket == settings.s3_pdf_bucket
    assert result.local_path == str(settings.temp_pdf_path / "report.pdf")
    assert (settings.temp_pdf_path / "report.pdf").read_bytes() == pdf_bytes


async def test_parse_pdf(env, pdf_bytes, tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(pdf_bytes)

    result = await env.run(parse_pdf, ParsePdfInput(task_id="t1", local_path=str(source)))

    assert "Quarterly Report" in result.markdown


async def test_upload_md(env, s3, settings):
    result = await env.run(upload_md, UploadMdInput(task_id="t1", markdown="# heading", key="report.md"))

    assert result.bucket == settings.s3_parsed_mds
    assert s3.objects[(settings.s3_parsed_mds, "report.md")] == b"# heading"


async def test_download_md(env, s3, settings):
    s3.objects[(settings.s3_parsed_mds, "report.md")] = b"# heading"

    result = await env.run(download_md, DownloadMdInput(task_id="t1", key="report.md"))

    assert result.bucket == settings.s3_parsed_mds
    assert result.local_path == str(settings.temp_md_path / "report.md")
    assert (settings.temp_md_path / "report.md").read_text() == "# heading"


async def test_activities_round_trip_through_both_buckets(env, s3, settings, pdf_bytes, tmp_path):
    """The five activities chain together the way a workflow would call them."""
    source = tmp_path / "report.pdf"
    source.write_bytes(pdf_bytes)

    await env.run(upload_pdf, UploadPdfInput(task_id="t1", local_path=str(source), key="report.pdf"))
    fetched = await env.run(download_pdf, DownloadPdfInput(task_id="t1", key="report.pdf"))
    parsed = await env.run(parse_pdf, ParsePdfInput(task_id="t1", local_path=fetched.local_path))
    await env.run(upload_md, UploadMdInput(task_id="t1", markdown=parsed.markdown, key="report.md"))
    final = await env.run(download_md, DownloadMdInput(task_id="t1", key="report.md"))

    assert "Quarterly Report" in open(final.local_path).read()


def test_all_activities_are_registered():
    """Every activity in the package is registered exactly once."""
    from activities import LEGAL_ACTIVITIES, PDF_ACTIVITIES

    assert len(PDF_ACTIVITIES) == 5
    assert len(LEGAL_ACTIVITIES) == 8
    # download_pdf is in both pipelines but registered once overall
    assert len(ALL_ACTIVITIES) == 12
    assert len(set(ALL_ACTIVITIES)) == len(ALL_ACTIVITIES)


@pytest.mark.parametrize("fn", ALL_ACTIVITIES)
def test_every_activity_is_decorated(fn):
    """Temporal stamps a definition onto anything wearing @activity.defn."""
    assert hasattr(fn, "__temporal_activity_definition")
