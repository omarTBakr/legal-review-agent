"""Asking questions about a finished review, with the S3 fake and the FakeLLM."""

import json

import pytest
from fastapi.testclient import TestClient

from enums.PromptName import PromptName
from enums.ReviewDecision import ReviewDecision
from enums.RiskSeverity import RiskSeverity
from main import app
from schemas.key_risk import KeyRisk
from utils.advice_store import advice_key, markdown_key
from utils.batching import split_pages_into_batches
from utils.chat_store import read_thread
from utils.projects import create_project, record_review

PAGES = [
    "This Agreement is made between Acme Ltd and Beta LLC.",
    "The Supplier shall indemnify the Customer against all losses.",
    "Liability under this Agreement is capped at twelve months of fees.",
]

ANSWER = "Liability is capped at twelve months of fees (p. 3)."


@pytest.fixture
def client(s3):
    return TestClient(app)


@pytest.fixture
def review(s3, settings):
    """A finished review in a project: advice and text stored, record written."""
    project = create_project("Acme", settings)
    pdf_key = f"{project.prefix}contract-abc123.pdf"

    risk = KeyRisk(
        description="Liability is capped at twelve months of fees",
        severity=RiskSeverity.MEDIUM,
        location="clause 9",
        quote="Liability under this Agreement is capped at twelve months of fees.",
        page=3,
        quote_verified=True,
    )
    document = {
        "task_id": "abc123",
        "pdf_key": pdf_key,
        "summary": "A services agreement.",
        "key_risks": [risk.to_dict()],
        "review_decision": ReviewDecision.AUTO_APPROVED.value,
        "question": "",
    }
    s3.objects[(settings.s3_projects, advice_key(pdf_key))] = json.dumps(document).encode()
    s3.objects[(settings.s3_projects, markdown_key(pdf_key))] = split_pages_into_batches(PAGES, 3)[0].markdown.encode()

    record_review(project.id, "abc123", "legal-review-abc123", [pdf_key], settings)

    return project, pdf_key


def ask(client, project_id, question="What is the liability cap?", **body):
    return client.post(f"/projects/{project_id}/reviews/abc123/chat", json={"question": question, **body})


# --- answering -----------------------------------------------------------


def test_a_question_is_answered(client, review, llm):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    body = ask(client, project.id).json()

    assert body["turn"]["answer"] == ANSWER
    assert body["turn_count"] == 1


def test_the_model_is_given_the_advice_and_the_matching_page(client, review, llm):
    project, pdf_key = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    ask(client, project.id)

    variables = llm.calls_for(PromptName.REVIEW_CHAT)[0]["variables"]
    assert "A services agreement." in variables["advice"]
    assert "capped at twelve months" in variables["pages"]
    assert f"{pdf_key}, page 3" in variables["pages"]
    assert variables["question"] == "What is the liability cap?"


def test_the_answer_cites_the_pages_it_was_given(client, review, llm):
    project, pdf_key = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    assert ask(client, project.id).json()["turn"]["citations"] == [f"{pdf_key} p. 3"]


def test_a_question_matching_no_page_gets_the_documents_themselves(client, review, llm):
    """Otherwise a question worded unlike the contract is answered from the summary."""
    project, pdf_key = review
    llm.script(PromptName.REVIEW_CHAT, "The documents do not cover that.")

    body = ask(client, project.id, question="zebra quantum harpsichord").json()

    variables = llm.calls_for(PromptName.REVIEW_CHAT)[0]["variables"]
    assert "No passage matched" in variables["pages"]
    assert "capped at twelve months" in variables["pages"]
    assert body["turn"]["citations"] == [f"{pdf_key} p. 1", f"{pdf_key} p. 2", f"{pdf_key} p. 3"]


def test_the_turn_is_stored_with_the_review(client, review, llm, settings):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    ask(client, project.id)

    [turn] = read_thread(project.id, "abc123", settings).turns
    assert turn.question == "What is the liability cap?"
    assert turn.answer == ANSWER


def test_earlier_turns_are_given_to_the_model(client, review, llm):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    ask(client, project.id)
    ask(client, project.id, question="And the notice period?")

    history = llm.calls_for(PromptName.REVIEW_CHAT)[1]["variables"]["history"]
    assert "What is the liability cap?" in history


def test_a_spoken_question_is_recorded_as_spoken(client, review, llm, settings):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    ask(client, project.id, spoken=True)

    assert read_thread(project.id, "abc123", settings).turns[0].spoken is True


def test_a_document_with_no_stored_text_still_answers_from_its_advice(client, review, llm, s3, settings):
    project, pdf_key = review
    del s3.objects[(settings.s3_projects, markdown_key(pdf_key))]
    llm.script(PromptName.REVIEW_CHAT, "From the review alone: the cap is twelve months.")

    body = ask(client, project.id)

    assert body.status_code == 200
    assert "A services agreement." in llm.calls_for(PromptName.REVIEW_CHAT)[0]["variables"]["advice"]


def test_the_recording_of_a_question_is_remembered_on_its_turn(client, review, llm, settings):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)
    key = f"{project.id}/chats/abc123/audio/0-question.wav"

    ask(client, project.id, spoken=True, question_audio=key)

    assert read_thread(project.id, "abc123", settings).turns[0].question_audio == key


def test_a_stored_recording_plays_back(client, review, llm, s3, settings):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)
    ask(client, project.id)
    s3.objects[(settings.s3_projects, f"{project.id}/chats/abc123/audio/0-answer.wav")] = b"RIFFfake"

    response = client.get(f"/projects/{project.id}/reviews/abc123/audio/0/answer")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFFfake"


def test_audio_that_was_never_kept_is_a_404(client, review, llm):
    project, _ = review

    assert client.get(f"/projects/{project.id}/reviews/abc123/audio/0/answer").status_code == 404


def test_only_questions_and_answers_can_be_played(client, review):
    project, _ = review

    assert client.get(f"/projects/{project.id}/reviews/abc123/audio/0/whistling").status_code == 404


# --- streaming -----------------------------------------------------------


def stream(client, project_id, question="What is the liability cap?", **body):
    return client.post(f"/projects/{project_id}/reviews/abc123/chat/stream", json={"question": question, **body})


def parse(response):
    """The (event, payload) pairs in a server-sent event stream."""
    events = []
    for block in response.text.split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if not lines:
            continue
        name = next(line[len("event:") :].strip() for line in lines if line.startswith("event:"))
        data = next(line[len("data:") :].strip() for line in lines if line.startswith("data:"))
        events.append((name, json.loads(data)))

    return events


def test_the_answer_arrives_as_events(client, review, llm):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    response = stream(client, project.id)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse(response)
    assert "".join(payload["text"] for name, payload in events if name == "delta") == ANSWER


def test_the_stream_closes_with_the_stored_turn(client, review, llm, settings):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    [(name, payload)] = [event for event in parse(stream(client, project.id)) if event[0] == "turn"]

    assert payload["turn"]["answer"] == ANSWER
    assert payload["turn_count"] == 1
    assert read_thread(project.id, "abc123", settings).turns[0].answer == ANSWER


def test_a_streamed_answer_cites_its_pages(client, review, llm):
    project, pdf_key = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)

    [(_, payload)] = [event for event in parse(stream(client, project.id)) if event[0] == "turn"]

    assert payload["turn"]["citations"] == [f"{pdf_key} p. 3"]


def test_a_recording_is_carried_onto_the_streamed_turn(client, review, llm, settings):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)
    key = f"{project.id}/chats/abc123/audio/0-question.wav"

    stream(client, project.id, spoken=True, question_audio=key)

    turn = read_thread(project.id, "abc123", settings).turns[0]
    assert turn.spoken is True and turn.question_audio == key


def test_a_bad_question_is_refused_before_the_stream_starts(client, review, llm):
    """A 400 is far more useful than an error event inside a 200."""
    project, _ = review

    assert stream(client, project.id, question="  ").status_code == 400


def test_an_unknown_review_is_refused_before_the_stream_starts(client, review, llm):
    project, _ = review

    assert client.post(f"/projects/{project.id}/reviews/nope/chat/stream", json={"question": "hi"}).status_code == 404


def test_a_failure_midway_becomes_an_error_event(client, review, llm):
    """By then the 200 has been sent, so the only way to say so is in the stream."""
    project, _ = review
    llm.error = RuntimeError("the model went away")

    events = parse(stream(client, project.id))

    assert [name for name, _ in events] == ["error"]
    assert "stopped" in events[0][1]["detail"]


def test_nothing_is_stored_when_the_answer_failed(client, review, llm, settings):
    project, _ = review
    llm.error = RuntimeError("the model went away")

    stream(client, project.id)

    assert read_thread(project.id, "abc123", settings).turns == []


# --- the thread ----------------------------------------------------------


def test_the_thread_reads_back(client, review, llm):
    project, _ = review
    llm.script(PromptName.REVIEW_CHAT, ANSWER)
    ask(client, project.id)

    body = client.get(f"/projects/{project.id}/reviews/abc123/chat").json()

    assert body["turn_count"] == 1
    assert body["turns"][0]["answer"] == ANSWER


def test_a_review_nobody_has_asked_about_has_an_empty_thread(client, review):
    project, _ = review

    assert client.get(f"/projects/{project.id}/reviews/abc123/chat").json()["turns"] == []


# --- refusals ------------------------------------------------------------


def test_an_empty_question_is_rejected(client, review, llm):
    project, _ = review

    assert ask(client, project.id, question="   ").status_code == 400


def test_a_very_long_question_is_rejected(client, review, llm):
    project, _ = review

    assert ask(client, project.id, question="a" * 2001).status_code == 400


def test_an_unknown_project_is_a_404(client, llm):
    assert ask(client, "nothing-here").status_code == 404


def test_an_unknown_review_is_a_404(client, review, llm):
    project, _ = review

    assert client.post(f"/projects/{project.id}/reviews/nope/chat", json={"question": "hi"}).status_code == 404


def test_a_review_with_no_advice_yet_is_reported_rather_than_answered(client, s3, settings, llm):
    """Answering from nothing would be worse than saying it is not ready."""
    project = create_project("Acme", settings)
    record_review(project.id, "abc123", "legal-review-abc123", [f"{project.prefix}contract.pdf"], settings)

    response = ask(client, project.id)

    assert response.status_code == 409
    assert llm.calls_for(PromptName.REVIEW_CHAT) == []
