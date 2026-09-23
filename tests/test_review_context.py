"""What a chat answer is grounded in, and why it is read once rather than per
question."""

import json

from enums.ReviewDecision import ReviewDecision
from enums.RiskSeverity import RiskSeverity
from schemas.key_risk import KeyRisk
from utils.advice_store import advice_key, markdown_key
from utils.batching import split_pages_into_batches
from utils.projects import create_project
from utils.review_context import clear_cache, load_context

PAGES = ["This Agreement is between Acme and Beta.", "Liability is capped at twelve months of fees."]


def stock(s3, settings, pdf_key, *, advice=True, text=True):
    """Puts one document's advice and text in the bucket, as a review would."""
    if advice:
        document = {
            "pdf_key": pdf_key,
            "summary": "A services agreement.",
            "key_risks": [KeyRisk(description="Unlimited liability", severity=RiskSeverity.HIGH, page=2).to_dict()],
            "review_decision": ReviewDecision.AUTO_APPROVED.value,
        }
        s3.objects[(settings.s3_projects, advice_key(pdf_key))] = json.dumps(document).encode()

    if text:
        s3.objects[(settings.s3_projects, markdown_key(pdf_key))] = split_pages_into_batches(PAGES, 2)[0].markdown.encode()


async def test_the_advice_and_the_text_come_back(s3, settings):
    project = create_project("Acme", settings)
    key = f"{project.prefix}contract.pdf"
    stock(s3, settings, key)

    context = await load_context(project.id, "abc123", [key], settings)

    assert "A services agreement." in context.advice[0]
    assert "Unlimited liability" in context.advice[0]
    assert "capped at twelve months" in context.documents[key]
    assert context.complete


async def test_every_document_is_read(s3, settings):
    project = create_project("Acme", settings)
    keys = [f"{project.prefix}one.pdf", f"{project.prefix}two.pdf"]
    for key in keys:
        stock(s3, settings, key)

    context = await load_context(project.id, "abc123", keys, settings)

    assert len(context.advice) == 2
    assert set(context.documents) == set(keys)


async def test_a_document_still_being_reviewed_is_reported_not_guessed(s3, settings):
    project = create_project("Acme", settings)
    done, waiting = f"{project.prefix}done.pdf", f"{project.prefix}waiting.pdf"
    stock(s3, settings, done)

    context = await load_context(project.id, "abc123", [done, waiting], settings)

    assert context.missing == [waiting]
    assert len(context.advice) == 1
    assert not context.complete


async def test_a_document_with_no_stored_text_still_brings_its_advice(s3, settings):
    project = create_project("Acme", settings)
    key = f"{project.prefix}contract.pdf"
    stock(s3, settings, key, text=False)

    context = await load_context(project.id, "abc123", [key], settings)

    assert context.advice and context.documents == {}
    assert context.complete


async def test_the_second_question_does_not_read_the_bucket_again(s3, settings):
    """A conversation asks many questions about the same review."""
    project = create_project("Acme", settings)
    key = f"{project.prefix}contract.pdf"
    stock(s3, settings, key)

    await load_context(project.id, "abc123", [key], settings)
    del s3.objects[(settings.s3_projects, advice_key(key))]

    again = await load_context(project.id, "abc123", [key], settings)

    assert again.advice, "the cached context should have answered without the bucket"


async def test_an_incomplete_review_is_not_cached(s3, settings):
    """Otherwise a document finishing mid-conversation would never be picked up."""
    project = create_project("Acme", settings)
    done, waiting = f"{project.prefix}done.pdf", f"{project.prefix}waiting.pdf"
    stock(s3, settings, done)

    await load_context(project.id, "abc123", [done, waiting], settings)
    stock(s3, settings, waiting)

    context = await load_context(project.id, "abc123", [done, waiting], settings)

    assert len(context.advice) == 2
    assert context.missing == []


async def test_two_reviews_do_not_share_a_context(s3, settings):
    project = create_project("Acme", settings)
    first, second = f"{project.prefix}first.pdf", f"{project.prefix}second.pdf"
    stock(s3, settings, first)
    stock(s3, settings, second)

    one = await load_context(project.id, "aaa", [first], settings)
    two = await load_context(project.id, "bbb", [second], settings)

    assert list(one.documents) == [first]
    assert list(two.documents) == [second]


async def test_clearing_the_cache_forces_a_fresh_read(s3, settings):
    project = create_project("Acme", settings)
    key = f"{project.prefix}contract.pdf"
    stock(s3, settings, key)

    await load_context(project.id, "abc123", [key], settings)
    clear_cache()
    del s3.objects[(settings.s3_projects, advice_key(key))]

    assert (await load_context(project.id, "abc123", [key], settings)).advice == []
