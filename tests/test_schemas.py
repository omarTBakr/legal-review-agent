"""Temporal serialises activity arguments as JSON, so every schema has to be a
plain dataclass that survives a round trip through the data converter."""

import dataclasses

import pytest
from temporalio.converter import DataConverter

from enums.RiskSeverity import RiskSeverity
from schemas.download_md import DownloadMdInput, DownloadMdOutput
from schemas.download_pdf import DownloadPdfInput, DownloadPdfOutput
from schemas.key_risk import KeyRisk
from schemas.parse_pdf import ParsePdfInput, ParsePdfOutput
from schemas.process_pdf import ProcessPdfInput
from schemas.process_pdf_result import ProcessPdfResult
from schemas.upload_md import UploadMdInput, UploadMdOutput
from schemas.upload_pdf import UploadPdfInput, UploadPdfOutput

SCHEMAS = [
    (UploadPdfInput, {"task_id": "t1", "local_path": "/tmp/a.pdf", "key": "a.pdf"}),
    (UploadPdfOutput, {"bucket": "pdfs", "key": "a.pdf"}),
    (DownloadPdfInput, {"task_id": "t1", "key": "a.pdf"}),
    (DownloadPdfOutput, {"bucket": "pdfs", "local_path": "/tmp/a.pdf"}),
    (ParsePdfInput, {"task_id": "t1", "local_path": "/tmp/a.pdf"}),
    (ParsePdfOutput, {"markdown": "# heading"}),
    (UploadMdInput, {"task_id": "t1", "markdown": "# heading", "key": "a.md"}),
    (UploadMdOutput, {"bucket": "mds", "key": "a.md"}),
    (DownloadMdInput, {"task_id": "t1", "key": "a.md"}),
    (ProcessPdfInput, {"task_id": "t1", "pdf_key": "a.pdf", "md_key": "a.md"}),
    (
        ProcessPdfResult,
        {
            "task_id": "t1",
            "pdf_bucket": "pdfs",
            "pdf_key": "a.pdf",
            "md_bucket": "mds",
            "md_key": "a.md",
            "local_pdf": "/tmp/a.pdf",
            "local_md": "/tmp/a.md",
            "markdown_characters": 80,
            "workflow_id": "process-pdf-a.pdf",
        },
    ),
    (DownloadMdOutput, {"bucket": "mds", "local_path": "/tmp/a.md"}),
    (
        KeyRisk,
        {
            "description": "Unlimited liability",
            "severity": RiskSeverity.HIGH,
            "location": "clause 9",
            "quote": "The Supplier's liability is unlimited.",
            "page": 3,
            "quote_verified": True,
        },
    ),
]


@pytest.mark.parametrize("schema,fields", SCHEMAS)
def test_is_a_dataclass(schema, fields):
    assert dataclasses.is_dataclass(schema)


@pytest.mark.parametrize("schema,fields", SCHEMAS)
async def test_survives_temporal_serialisation(schema, fields):
    instance = schema(**fields)
    converter = DataConverter.default

    payloads = await converter.encode([instance])
    decoded = await converter.decode(payloads, [schema])

    assert decoded[0] == instance


async def test_a_key_risk_stored_before_quotes_existed_still_loads():
    """Workflows already running carry risks with no quote, page or verification."""
    converter = DataConverter.default
    old = KeyRisk(description="Unlimited liability", severity=RiskSeverity.HIGH, location="clause 9")
    payloads = await converter.encode([{"description": "Unlimited liability", "severity": "high", "location": "clause 9"}])

    [decoded] = await converter.decode(payloads, [KeyRisk])

    assert decoded == old
    assert decoded.quote == "" and decoded.page is None and decoded.quote_verified is False


def test_stdlib_dataclasses_is_not_shadowed():
    """A top-level package named `dataclasses` would break pydantic and temporalio."""
    assert dataclasses.__file__.endswith("lib/python3.12/dataclasses.py")
