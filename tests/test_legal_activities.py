"""Each legal activity through Temporal's ActivityEnvironment, with the FakeLLM
standing in for the model."""

import asyncio
import importlib
import json
import time

import pytest
from temporalio.testing import ActivityEnvironment

from activities import (
    analyze_batch,
    cleanup_scratch,
    human_followup,
    merge_advice,
    send_report,
    split_pages,
    upload_advice,
)
from enums.PromptName import PromptName
from enums.ReviewDecision import ReviewDecision
from enums.RiskSeverity import RiskSeverity
from exceptions.llm import LLMResponseError
from exceptions.notification import EmailSendError
from exceptions.storage import UploadError
from schemas.analyze_batch import AnalyzeBatchInput
from schemas.cleanup_scratch import CleanupScratchInput
from schemas.human_followup import HumanFollowupInput
from schemas.key_risk import KeyRisk
from schemas.legal_advice import LegalAdvice
from schemas.legal_review import DocumentAdvice
from schemas.merge_advice import MergeAdviceInput
from schemas.page_batch import PageBatch
from schemas.send_report import SendReportInput
from schemas.split_pages import SplitPagesInput
from schemas.upload_advice import UploadAdviceInput
from utils.advice_store import markdown_key


@pytest.fixture
def env():
    return ActivityEnvironment()


BATCH = PageBatch(index=0, first_page=1, last_page=2, markdown="Clause 1. Unlimited liability.")

ADVICE = LegalAdvice(
    summary="A services agreement.",
    key_risks=[
        KeyRisk(
            description="Unlimited liability",
            severity=RiskSeverity.HIGH,
            location="clause 9",
            quote="Clause 1. Unlimited liability.",
            page=1,
            confidence=0.9,
            category="liability",
            recommended_action="Negotiate a liability cap.",
        )
    ],
)

DOCUMENT = DocumentAdvice(pdf_key="contract-a1b2c3d4.pdf", advice=ADVICE)


# --- split_pages ---------------------------------------------------------


async def test_split_pages_batches_a_real_pdf(env, settings, multi_page_pdf_bytes, tmp_path):
    source = tmp_path / "contract.pdf"
    source.write_bytes(multi_page_pdf_bytes)

    result = await env.run(
        split_pages,
        SplitPagesInput(task_id="t1", pdf_key="contract.pdf", local_pdf=str(source), pages_per_batch=2),
    )

    assert result.page_count == 6
    assert len(result.batches) == 3
    assert result.batches[0].label == "pages 1-2"


async def test_split_pages_keeps_the_document_text(env, multi_page_pdf_bytes, tmp_path):
    source = tmp_path / "contract.pdf"
    source.write_bytes(multi_page_pdf_bytes)

    result = await env.run(
        split_pages,
        SplitPagesInput(task_id="t1", pdf_key="contract.pdf", local_pdf=str(source), pages_per_batch=2),
    )

    joined = " ".join(b.markdown for b in result.batches)
    assert "Clause 1" in joined and "Clause 6" in joined


async def test_split_pages_does_not_block_the_event_loop(env, monkeypatch):
    """With several documents in flight, one parse must not stall the model calls of the others."""

    def slow_parse(source):
        time.sleep(0.3)
        return ["page one"]

    # the package re-exports the activity function under the module's name
    monkeypatch.setattr(importlib.import_module("activities.split_pages"), "parse_pdf_pages", slow_parse)

    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        result = await env.run(
            split_pages,
            SplitPagesInput(task_id="t1", pdf_key="contract.pdf", local_pdf="unused.pdf", pages_per_batch=10),
        )
    finally:
        ticker.cancel()

    assert result.page_count == 1
    # a parse run on the loop itself would leave the ticker where it started
    assert ticks >= 10


async def test_split_pages_stores_the_document_text(env, settings, s3, multi_page_pdf_bytes, tmp_path):
    """Chat answers questions from the text later, so the review keeps it."""
    source = tmp_path / "contract.pdf"
    source.write_bytes(multi_page_pdf_bytes)

    result = await env.run(
        split_pages,
        SplitPagesInput(task_id="t1", pdf_key="contract-abc123.pdf", local_pdf=str(source), pages_per_batch=2),
    )

    assert result.md_key == markdown_key("contract-abc123.pdf")
    stored = s3.objects[(settings.s3_parsed_mds, result.md_key)].decode()
    assert "Clause 1" in stored and "Clause 6" in stored


async def test_a_projects_text_goes_to_its_own_bucket(env, settings, s3, multi_page_pdf_bytes, tmp_path):
    source = tmp_path / "contract.pdf"
    source.write_bytes(multi_page_pdf_bytes)

    result = await env.run(
        split_pages,
        SplitPagesInput(
            task_id="t1",
            pdf_key="acme-b4b731b4/contract-abc123.pdf",
            local_pdf=str(source),
            pages_per_batch=2,
            md_bucket=settings.s3_projects,
        ),
    )

    assert (settings.s3_projects, result.md_key) in s3.objects


async def test_a_review_survives_the_text_not_being_stored(env, settings, s3, multi_page_pdf_bytes, tmp_path, monkeypatch):
    """Losing the text costs chat its quotes; it must not cost the review."""
    source = tmp_path / "contract.pdf"
    source.write_bytes(multi_page_pdf_bytes)

    def refuse(*args, **kwargs):
        raise UploadError("bucket is read-only")

    monkeypatch.setattr(importlib.import_module("activities.split_pages"), "upload_s3_file", refuse)

    result = await env.run(
        split_pages,
        SplitPagesInput(task_id="t1", pdf_key="contract-abc123.pdf", local_pdf=str(source), pages_per_batch=2),
    )

    assert result.md_key == ""
    assert len(result.batches) == 3


# --- analyze_batch -------------------------------------------------------


async def test_analyze_batch_returns_validated_advice(env, llm):
    result = await env.run(
        analyze_batch,
        AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=BATCH, batch_count=1),
    )

    assert result.advice.summary
    assert result.advice.key_risks[0].severity is RiskSeverity.HIGH


async def test_analyze_batch_sends_the_batch_to_the_model(env, llm):
    await env.run(
        analyze_batch,
        AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=BATCH, batch_count=3),
    )

    call = llm.calls_for(PromptName.LEGAL_ADVICE)[0]
    assert call["variables"]["markdown"] == BATCH.markdown
    assert call["variables"]["batch_label"] == "pages 1-2"
    assert call["variables"]["batch_count"] == 3


async def test_analyze_batch_rejects_advice_with_no_summary(env, llm):
    llm.script(PromptName.LEGAL_ADVICE, {"key_risks": []})

    with pytest.raises(LLMResponseError, match="unusable"):
        await env.run(
            analyze_batch,
            AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=BATCH, batch_count=1),
        )


async def test_analyze_batch_rejects_a_risk_with_missing_quality_fields(env, llm):
    """
    A finding without the complete quality contract must fail the batch.
    """
    llm.script(
        PromptName.LEGAL_ADVICE,
        {
            "summary": "ok",
            "key_risks": [
                {
                    "description": "unlimited liability",
                    "severity": "critical",
                    "quote": "x",
                    "page": 1,
                    "confidence": 0.8,
                    "category": "liability",
                    "recommended_action": "Negotiate a cap.",
                },
                {"description": "d", "severity": "apocalyptic"},
            ],
        },
    )

    with pytest.raises(LLMResponseError, match="unusable"):
        await env.run(
            analyze_batch,
            AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=BATCH, batch_count=1),
        )


async def test_analyze_batch_still_rejects_a_reply_of_the_wrong_shape(env, llm):
    """
    The line: a bad *risk* is dropped, a bad *reply* is retried. No summary
    means the model did not follow the format, and asking again is the move.
    """
    llm.script(PromptName.LEGAL_ADVICE, {"key_risks": []})

    with pytest.raises(LLMResponseError):
        await env.run(
            analyze_batch,
            AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=BATCH, batch_count=1),
        )


async def test_analyze_batch_rejects_a_question_with_no_question(env, llm):
    llm.script(PromptName.LEGAL_ADVICE, {"summary": "ok", "needs_human": True, "question": ""})

    with pytest.raises(LLMResponseError):
        await env.run(
            analyze_batch,
            AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=BATCH, batch_count=1),
        )


QUOTED_BATCH = PageBatch(
    index=0,
    first_page=4,
    last_page=5,
    markdown="<!-- page 4 -->\n\nRecitals.\n\n<!-- page 5 -->\n\n9. The Supplier's liability is unlimited.",
)


async def test_analyze_batch_verifies_a_real_quote_and_finds_its_page(env, llm):
    llm.script(
        PromptName.LEGAL_ADVICE,
        {
            "summary": "ok",
            "key_risks": [
                {
                    "description": "d",
                    "severity": "high",
                    "quote": "The Supplier's liability is unlimited.",
                    "page": 4,
                    "confidence": 0.9,
                    "category": "liability",
                    "recommended_action": "Negotiate a liability cap.",
                },
            ],
        },
    )

    result = await env.run(
        analyze_batch,
        AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=QUOTED_BATCH, batch_count=1),
    )

    [risk] = result.advice.key_risks
    assert risk.quote_verified
    assert risk.page == 5


async def test_analyze_batch_flags_an_invented_quote_but_keeps_the_risk(env, llm):
    llm.script(
        PromptName.LEGAL_ADVICE,
        {
            "summary": "ok",
            "key_risks": [
                {
                    "description": "d",
                    "severity": "high",
                    "quote": "The Customer owes ten million pounds.",
                    "page": 5,
                    "confidence": 0.4,
                    "category": "payment",
                    "recommended_action": "Verify the payment obligation.",
                }
            ],
        },
    )

    result = await env.run(
        analyze_batch,
        AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=QUOTED_BATCH, batch_count=1),
    )

    [risk] = result.advice.key_risks
    assert not risk.quote_verified
    assert risk.page == 5


async def test_the_model_cannot_mark_its_own_quote_verified(env, llm):
    llm.script(
        PromptName.LEGAL_ADVICE,
        {
            "summary": "ok",
            "key_risks": [
                {
                    "description": "d",
                    "severity": "high",
                    "quote": "Nothing like this is in it.",
                    "page": 5,
                    "confidence": 0.2,
                    "category": "other",
                    "recommended_action": "Review manually.",
                    "quote_verified": True,
                }
            ],
        },
    )

    result = await env.run(
        analyze_batch,
        AnalyzeBatchInput(task_id="t1", pdf_key="contract.pdf", batch=QUOTED_BATCH, batch_count=1),
    )

    assert not result.advice.key_risks[0].quote_verified


# --- merge_advice --------------------------------------------------------

VERIFIED = LegalAdvice(
    summary="Part.",
    key_risks=[
        KeyRisk(
            description="Unlimited liability",
            severity=RiskSeverity.HIGH,
            quote="The Supplier's liability is unlimited.",
            page=5,
            confidence=0.9,
            category="liability",
            recommended_action="Negotiate a liability cap.",
            quote_verified=True,
        )
    ],
)


async def test_the_merge_prompt_carries_quotes_and_pages(env, llm):
    llm.script(PromptName.MERGE_ADVICE, {"summary": "Merged.", "key_risks": []})

    await env.run(merge_advice, MergeAdviceInput(task_id="t1", pdf_key="a.pdf", parts=[VERIFIED, ADVICE]))

    parts_text = llm.calls_for(PromptName.MERGE_ADVICE)[0]["variables"]["parts"]
    assert "The Supplier's liability is unlimited." in parts_text
    assert '"page": 5' in parts_text
    assert "quote_verified" not in parts_text


async def test_a_quote_the_merge_kept_intact_stays_verified(env, llm):
    llm.script(
        PromptName.MERGE_ADVICE,
        {
            "summary": "Merged.",
            "key_risks": [
                {
                    "description": "kept",
                    "severity": "high",
                    "quote": "The Supplier's liability is unlimited.",
                    "page": 5,
                    "confidence": 0.9,
                    "category": "liability",
                    "recommended_action": "Negotiate a cap.",
                },
                {
                    "description": "rewritten",
                    "severity": "low",
                    "quote": "Liability has no cap at all, it seems.",
                    "page": 5,
                    "confidence": 0.4,
                    "category": "liability",
                    "recommended_action": "Review manually.",
                },
            ],
        },
    )

    result = await env.run(merge_advice, MergeAdviceInput(task_id="t1", pdf_key="a.pdf", parts=[VERIFIED, ADVICE]))

    kept, rewritten = result.advice.key_risks
    assert kept.quote_verified and kept.page == 5
    assert not rewritten.quote_verified


async def test_merging_one_part_skips_the_model(env, llm):
    """A single-batch document cannot be improved by a merge, so don't pay for one."""
    result = await env.run(merge_advice, MergeAdviceInput(task_id="t1", pdf_key="a.pdf", parts=[ADVICE]))

    assert result.advice is ADVICE
    assert llm.calls_for(PromptName.MERGE_ADVICE) == []


async def test_merging_several_parts_calls_the_model(env, llm):
    llm.script(PromptName.MERGE_ADVICE, {"summary": "Merged.", "key_risks": []})

    result = await env.run(merge_advice, MergeAdviceInput(task_id="t1", pdf_key="a.pdf", parts=[ADVICE, ADVICE]))

    assert result.advice.summary == "Merged."
    assert len(llm.calls_for(PromptName.MERGE_ADVICE)) == 1


async def test_the_merge_prompt_receives_every_part(env, llm):
    llm.script(PromptName.MERGE_ADVICE, {"summary": "Merged.", "key_risks": []})

    await env.run(merge_advice, MergeAdviceInput(task_id="t1", pdf_key="a.pdf", parts=[ADVICE, ADVICE]))

    parts_text = llm.calls_for(PromptName.MERGE_ADVICE)[0]["variables"]["parts"]
    assert parts_text.count("Unlimited liability") == 4


async def test_merging_nothing_is_an_error(env, llm):
    with pytest.raises(LLMResponseError, match="nothing to merge"):
        await env.run(merge_advice, MergeAdviceInput(task_id="t1", pdf_key="a.pdf", parts=[]))


# --- human_followup ------------------------------------------------------


async def test_followup_revises_the_advice(env, llm):
    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Revised for UK law.", "key_risks": []})

    result = await env.run(
        human_followup,
        HumanFollowupInput(task_id="t1", pdf_key="a.pdf", advice=ADVICE, question="Which law?", answer="UK"),
    )

    assert result.advice.summary == "Revised for UK law."


async def test_followup_marks_the_advice_human_approved(env, llm):
    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Revised.", "key_risks": []})

    result = await env.run(
        human_followup,
        HumanFollowupInput(task_id="t1", pdf_key="a.pdf", advice=ADVICE, question="Which law?", answer="UK"),
    )

    assert result.advice.review_decision is ReviewDecision.HUMAN_APPROVED
    assert result.advice.review_decision.was_seen_by_a_human


async def test_followup_clears_the_question(env, llm):
    """Otherwise a document could bounce between model and human forever."""
    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Revised.", "key_risks": [], "needs_human": True, "question": "again?"})

    result = await env.run(
        human_followup,
        HumanFollowupInput(task_id="t1", pdf_key="a.pdf", advice=ADVICE, question="Which law?", answer="UK"),
    )

    assert result.advice.needs_human is False
    assert result.advice.question == ""


async def test_followup_gives_the_model_the_answer(env, llm):
    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Revised.", "key_risks": []})

    await env.run(
        human_followup,
        HumanFollowupInput(task_id="t1", pdf_key="a.pdf", advice=ADVICE, question="Which law?", answer="UK law"),
    )

    variables = llm.calls_for(PromptName.HUMAN_FOLLOWUP)[0]["variables"]
    assert variables["answer"] == "UK law"
    assert "Unlimited liability" in variables["draft"]


async def test_followup_keeps_verification_for_quotes_it_left_alone(env, llm):
    llm.script(
        PromptName.HUMAN_FOLLOWUP,
        {
            "summary": "Revised.",
            "key_risks": [
                {
                    "description": "still there",
                    "severity": "critical",
                    "quote": "The Supplier's liability is unlimited.",
                    "page": 5,
                    "confidence": 0.9,
                    "category": "liability",
                    "recommended_action": "Negotiate a cap.",
                }
            ],
        },
    )

    result = await env.run(
        human_followup,
        HumanFollowupInput(task_id="t1", pdf_key="a.pdf", advice=VERIFIED, question="Which law?", answer="UK"),
    )

    [risk] = result.advice.key_risks
    assert risk.quote_verified and risk.page == 5
    assert "The Supplier's liability is unlimited." in llm.calls_for(PromptName.HUMAN_FOLLOWUP)[0]["variables"]["draft"]


# --- cleanup_scratch -----------------------------------------------------


async def test_cleanup_removes_the_local_copies(env, tmp_path):
    files = [tmp_path / "one.pdf", tmp_path / "two.md"]
    for path in files:
        path.write_bytes(b"scratch")

    result = await env.run(
        cleanup_scratch,
        CleanupScratchInput(task_id="t1", pdf_key="contract.pdf", paths=[str(path) for path in files]),
    )

    assert result.removed == 2
    assert not any(path.exists() for path in files)


async def test_cleanup_does_not_mind_a_file_that_is_already_gone(env, tmp_path):
    """Two documents can share a path when a review is retried."""
    result = await env.run(
        cleanup_scratch,
        CleanupScratchInput(task_id="t1", pdf_key="contract.pdf", paths=[str(tmp_path / "never-existed.pdf")]),
    )

    assert result.removed == 1 and result.failed == 0


async def test_cleanup_reports_what_it_could_not_remove_rather_than_failing(env, tmp_path):
    """The advice is already stored; a locked file must not fail the review."""
    result = await env.run(
        cleanup_scratch,
        CleanupScratchInput(task_id="t1", pdf_key="contract.pdf", paths=[str(tmp_path)]),
    )

    assert result.failed == 1


# --- send_report ---------------------------------------------------------


def sent_through(monkeypatch) -> list:
    """Captures what send_report hands the mailer, instead of sending it."""
    # the package re-exports the activity under the module's name
    module = importlib.import_module("activities.send_report")

    calls = []

    def fake_send(recipient, subject, html, settings, attachments=()):
        calls.append({"recipient": recipient, "subject": subject, "html": html, "attachments": attachments})

    monkeypatch.setattr(module, "send_email", fake_send)
    return calls


async def test_send_report_emails_the_review(env, monkeypatch):
    calls = sent_through(monkeypatch)

    result = await env.run(
        send_report,
        SendReportInput(task_id="t1", recipient="legal@acme.test", documents=[DOCUMENT], project_name="Acme"),
    )

    assert result.sent and result.recipient == "legal@acme.test"
    assert calls[0]["recipient"] == "legal@acme.test"
    assert "Unlimited liability" in calls[0]["html"]
    assert calls[0]["attachments"][0][0] == "legal-review-t1.json"


async def test_send_report_does_nothing_without_a_recipient(env, monkeypatch):
    calls = sent_through(monkeypatch)

    result = await env.run(send_report, SendReportInput(task_id="t1", recipient="", documents=[DOCUMENT]))

    assert not result.sent and calls == []


async def test_send_report_reports_a_missing_mail_server_rather_than_failing(env):
    """The review succeeded; retrying cannot conjure an SMTP server."""
    result = await env.run(
        send_report,
        SendReportInput(task_id="t1", recipient="legal@acme.test", documents=[DOCUMENT]),
    )

    assert not result.sent
    assert "SMTP_HOST" in result.reason


async def test_a_refused_message_fails_the_activity_so_temporal_retries(env, monkeypatch):
    def refuse(*args, **kwargs):
        raise EmailSendError("mailbox full")

    monkeypatch.setattr(importlib.import_module("activities.send_report"), "send_email", refuse)

    with pytest.raises(EmailSendError):
        await env.run(
            send_report,
            SendReportInput(task_id="t1", recipient="legal@acme.test", documents=[DOCUMENT]),
        )


# --- upload_advice -------------------------------------------------------


async def test_upload_advice_stores_the_evidence(env, s3, settings):
    result = await env.run(upload_advice, UploadAdviceInput(task_id="t1", pdf_key="contract.pdf", advice=VERIFIED))

    [stored] = json.loads(s3.objects[(result.bucket, result.key)])["key_risks"]
    assert stored["quote"] == "The Supplier's liability is unlimited."
    assert stored["page"] == 5
    assert stored["quote_verified"] is True


async def test_upload_advice_stores_json(env, s3, settings):
    result = await env.run(upload_advice, UploadAdviceInput(task_id="t1", pdf_key="contract.pdf", advice=ADVICE))

    assert result.bucket == settings.s3_legal_advice
    assert result.key == "contract.advice.json"
    assert result.s3_path == f"s3://{settings.s3_legal_advice}/contract.advice.json"


async def test_the_stored_document_is_readable(env, s3, settings):
    await env.run(upload_advice, UploadAdviceInput(task_id="t1", pdf_key="contract.pdf", advice=ADVICE))

    stored = json.loads(s3.objects[(settings.s3_legal_advice, "contract.advice.json")])
    assert stored["summary"] == ADVICE.summary
    assert stored["key_risks"][0]["severity"] == "high"
    assert stored["review_decision"] == "auto_approved"
