"""LegalReviewWorkflow against a real (in-process) Temporal server, with the
activities running for real against the in-memory S3 fake and the FakeLLM.

If the test server cannot be started the whole module skips rather than failing
the suite.
"""

import asyncio
import importlib
import uuid

import pytest
import pytest_asyncio
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities import LEGAL_ACTIVITIES
from enums.PromptName import PromptName
from enums.ReviewDecision import ReviewDecision
from enums.TaskStatus import TaskStatus
from exceptions.notification import EmailSendError
from schemas.legal_review import LegalReviewInput
from workflows import LEGAL_WORKFLOWS
from workflows.workflow_legal_review import LegalReviewWorkflow

# the Temporal server and its client are created once for the module, so every
# test in here has to run on that same event loop
pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal_env():
    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"temporal test server unavailable: {exc}")
    yield env
    await env.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def worker(temporal_env):
    """A worker polling a queue unique to this test, so tests cannot cross-talk."""
    task_queue = f"legal-test-{uuid.uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=task_queue,
        workflows=LEGAL_WORKFLOWS,
        activities=LEGAL_ACTIVITIES,
    ):
        yield temporal_env.client, task_queue


def stock_the_bucket(s3, settings, pdf_bytes, count):
    keys = [f"doc{i}.pdf" for i in range(count)]
    for key in keys:
        s3.objects[(settings.s3_pdf_bucket, key)] = pdf_bytes
    return keys


async def start(worker, keys, **overrides):
    client, task_queue = worker
    # the defaults a test does not override; an override must replace, not collide
    options = {"pages_per_batch": 2, "max_concurrent_pdfs": 2} | overrides
    payload = LegalReviewInput(task_id="t1", pdf_keys=keys, **options)
    return await client.start_workflow(
        LegalReviewWorkflow.run,
        payload,
        id=f"legal-test-{uuid.uuid4()}",
        task_queue=task_queue,
    )


async def run(worker, keys, **overrides):
    return await (await start(worker, keys, **overrides)).result()


# --- the happy path ------------------------------------------------------


async def test_every_document_is_reviewed(worker, s3, settings, llm, multi_page_pdf_bytes):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 3)

    result = await run(worker, keys)

    assert result.document_count == 3
    assert {doc.pdf_key for doc in result.documents} == set(keys)


async def test_each_document_gets_advice_and_an_s3_path(worker, s3, settings, llm, multi_page_pdf_bytes):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 2)

    result = await run(worker, keys)

    for doc in result.documents:
        assert doc.advice.summary
        assert doc.advice.s3_path.startswith(f"s3://{settings.s3_legal_advice}/")


async def test_the_advice_lands_in_the_advice_bucket(worker, s3, settings, llm, multi_page_pdf_bytes):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)

    await run(worker, keys)

    stored = [key for bucket, key in s3.objects if bucket == settings.s3_legal_advice]
    assert stored == ["doc0.advice.json"]


async def test_a_long_document_is_split_into_batches(worker, s3, settings, llm, multi_page_pdf_bytes):
    """Six pages at two per batch is three LLM calls, then one merge."""
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)

    await run(worker, keys)

    assert len(llm.calls_for(PromptName.LEGAL_ADVICE)) == 3
    assert len(llm.calls_for(PromptName.MERGE_ADVICE)) == 1


# --- the concurrency cap -------------------------------------------------


async def test_no_more_than_two_documents_are_in_flight(worker, s3, settings, llm, multi_page_pdf_bytes):
    """The cap is the whole point: six documents must never exceed two at once."""
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 6)
    llm.delay = 0.05

    await run(worker, keys)

    assert llm.max_in_flight <= 2


async def test_the_cap_is_configurable(worker, s3, settings, llm, multi_page_pdf_bytes):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 4)
    llm.delay = 0.05

    await run(worker, keys, max_concurrent_pdfs=1)

    assert llm.max_in_flight == 1


async def test_more_documents_than_the_cap_still_all_finish(worker, s3, settings, llm, multi_page_pdf_bytes):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 5)

    result = await run(worker, keys)

    assert result.document_count == 5


# --- human in the loop ---------------------------------------------------


NEEDS_HUMAN = {
    "summary": "An agreement with an open question.",
    "key_risks": [{"description": "Governing law unstated", "severity": "high", "location": "clause 1"}],
    "needs_human": True,
    "question": "Which jurisdiction governs this contract?",
}


async def test_the_workflow_waits_for_a_human(worker, s3, settings, llm, multi_page_pdf_bytes):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)
    llm.script(PromptName.LEGAL_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.MERGE_ADVICE, NEEDS_HUMAN)

    handle = await start(worker, keys)

    async def question_arrives():
        while not await handle.query(LegalReviewWorkflow.pending_questions):
            await asyncio.sleep(0.05)

    await asyncio.wait_for(question_arrives(), timeout=20)

    pending = await handle.query(LegalReviewWorkflow.pending_questions)
    assert pending[0]["pdf_key"] == "doc0.pdf"
    assert "jurisdiction" in pending[0]["question"]

    progress = await handle.query(LegalReviewWorkflow.progress)
    assert progress["doc0.pdf"] == TaskStatus.AWAITING_HUMAN.value

    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Governed by English law.", "key_risks": []})
    await handle.signal(LegalReviewWorkflow.human_response, args=["doc0.pdf", "English law"])

    result = await handle.result()
    assert result.documents[0].advice.summary == "Governed by English law."
    assert result.documents[0].advice.review_decision is ReviewDecision.HUMAN_APPROVED


async def test_the_human_answer_reaches_the_model(worker, s3, settings, llm, multi_page_pdf_bytes):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)
    llm.script(PromptName.LEGAL_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.MERGE_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Revised.", "key_risks": []})

    handle = await start(worker, keys)

    async def question_arrives():
        while not await handle.query(LegalReviewWorkflow.pending_questions):
            await asyncio.sleep(0.05)

    await asyncio.wait_for(question_arrives(), timeout=20)
    await handle.signal(LegalReviewWorkflow.human_response, args=["doc0.pdf", "English law"])
    await handle.result()

    assert llm.calls_for(PromptName.HUMAN_FOLLOWUP)[0]["variables"]["answer"] == "English law"


async def test_nobody_answering_continues_and_flags_it(worker, s3, settings, llm, multi_page_pdf_bytes):
    """The work already done must not be thrown away because a human was busy."""
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)
    llm.script(PromptName.LEGAL_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.MERGE_ADVICE, NEEDS_HUMAN)

    result = await run(worker, keys, human_input_timeout_seconds=1)

    advice = result.documents[0].advice
    assert advice.review_decision is ReviewDecision.UNREVIEWED_TIMEOUT
    assert advice.review_decision.needs_attention
    assert advice.summary == NEEDS_HUMAN["summary"]
    # no follow-up happened, because nobody answered
    assert llm.calls_for(PromptName.HUMAN_FOLLOWUP) == []


async def test_an_answer_for_an_unknown_document_is_ignored(worker, s3, settings, llm, multi_page_pdf_bytes):
    """A stray signal must not corrupt the run."""
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)

    handle = await start(worker, keys)
    await handle.signal(LegalReviewWorkflow.human_response, args=["not-a-document.pdf", "hello"])

    result = await handle.result()
    assert result.document_count == 1


async def test_a_document_waiting_on_a_human_gives_up_its_slot(worker, s3, settings, llm, multi_page_pdf_bytes):
    """
    With one slot and two documents that both have questions, the second must
    still get analysed while the first waits; if the wait held the slot, only
    one question could ever be pending and this would never finish.
    """
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 2)
    llm.script(PromptName.LEGAL_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.MERGE_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Revised.", "key_risks": []})
    llm.delay = 0.05

    handle = await start(worker, keys, max_concurrent_pdfs=1)

    async def both_questions_arrive():
        while len(await handle.query(LegalReviewWorkflow.pending_questions)) < 2:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(both_questions_arrive(), timeout=30)

    for key in keys:
        await handle.signal(LegalReviewWorkflow.human_response, args=[key, "English law"])

    result = await handle.result()
    assert {doc.advice.review_decision for doc in result.documents} == {ReviewDecision.HUMAN_APPROVED}
    # taking the slot back for the revision keeps the cap
    assert llm.max_in_flight == 1


async def test_each_document_is_published_as_soon_as_it_finishes(worker, s3, settings, llm, multi_page_pdf_bytes):
    """A finished document's advice must be readable while another is still in flight."""
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 2)
    llm.script(PromptName.LEGAL_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.MERGE_ADVICE, NEEDS_HUMAN)
    llm.script(PromptName.HUMAN_FOLLOWUP, {"summary": "Revised.", "key_risks": []})

    handle = await start(worker, keys)

    async def both_questions_arrive():
        while len(await handle.query(LegalReviewWorkflow.pending_questions)) < 2:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(both_questions_arrive(), timeout=30)
    assert await handle.query(LegalReviewWorkflow.finished_documents) == []

    # only the first document is released; the second keeps the run going
    await handle.signal(LegalReviewWorkflow.human_response, args=[keys[0], "English law"])

    async def first_document_published():
        while not await handle.query(LegalReviewWorkflow.finished_documents):
            await asyncio.sleep(0.05)

    await asyncio.wait_for(first_document_published(), timeout=30)

    finished = await handle.query(LegalReviewWorkflow.finished_documents)
    assert [doc.pdf_key for doc in finished] == [keys[0]]
    assert finished[0].advice.summary == "Revised."
    # stored before it is published, so the path is already there
    assert finished[0].advice.s3_path.startswith(f"s3://{settings.s3_legal_advice}/")

    await handle.signal(LegalReviewWorkflow.human_response, args=[keys[1], "English law"])
    result = await handle.result()
    assert result.document_count == 2


# --- the emailed report --------------------------------------------------


@pytest.fixture
def mailbox(monkeypatch):
    """Catches the report instead of sending it."""
    module = importlib.import_module("activities.send_report")
    sent = []

    def fake_send(recipient, subject, html, settings, attachments=()):
        sent.append({"recipient": recipient, "subject": subject, "html": html})

    monkeypatch.setattr(module, "send_email", fake_send)
    return sent


async def test_the_report_is_emailed_when_an_address_was_given(worker, s3, settings, llm, multi_page_pdf_bytes, mailbox):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 2)

    await run(worker, keys, report_email="legal@acme.test", project_name="Acme")

    assert len(mailbox) == 1
    assert mailbox[0]["recipient"] == "legal@acme.test"
    assert "Acme" in mailbox[0]["subject"]


async def test_no_address_means_no_email(worker, s3, settings, llm, multi_page_pdf_bytes, mailbox):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)

    await run(worker, keys)

    assert mailbox == []


async def test_the_report_covers_every_document(worker, s3, settings, llm, multi_page_pdf_bytes, mailbox):
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 3)

    await run(worker, keys, report_email="legal@acme.test")

    for key in keys:
        assert key in mailbox[0]["html"]


async def test_a_review_still_succeeds_when_the_email_cannot_be_sent(
    worker, s3, settings, llm, multi_page_pdf_bytes, monkeypatch
):
    """The advice is in the bucket; losing it over a mail server would be absurd."""
    module = importlib.import_module("activities.send_report")

    def refuse(*args, **kwargs):
        raise EmailSendError("mailbox full")

    monkeypatch.setattr(module, "send_email", refuse)
    keys = stock_the_bucket(s3, settings, multi_page_pdf_bytes, 1)

    result = await run(worker, keys, report_email="legal@acme.test")

    assert result.document_count == 1


# --- failures ------------------------------------------------------------


async def test_a_missing_pdf_fails_the_review(worker, s3, llm):
    with pytest.raises(WorkflowFailureError):
        await run(worker, ["does-not-exist.pdf"])


async def test_no_documents_produces_an_empty_result(worker, s3, llm):
    result = await run(worker, [])

    assert result.document_count == 0
