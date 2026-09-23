"""The chat thread, stored beside its review in the bucket."""

import json

import pytest

from exceptions.validation import ValidationError
from schemas.chat import ChatThread, ChatTurn
from utils.chat_store import append_turn, read_thread, thread_key
from utils.projects import create_project


def turn(question="What is the cap?", answer="Twelve months of fees (p. 3).") -> ChatTurn:
    return ChatTurn(question=question, answer=answer, citations=["contract.pdf p. 3"])


def test_a_review_nobody_has_asked_about_reads_as_empty(s3, settings):
    thread = read_thread("acme-b4b731b4", "abc123", settings)

    assert thread.turns == []
    assert thread.task_id == "abc123"


def test_a_turn_is_stored_under_the_project(s3, settings):
    project = create_project("Acme", settings)

    append_turn(project.id, "abc123", turn(), settings)

    stored = json.loads(s3.objects[(settings.s3_projects, thread_key(project.id, "abc123"))])
    assert stored["turns"][0]["question"] == "What is the cap?"
    assert stored["turn_count"] == 1


def test_a_thread_round_trips(s3, settings):
    project = create_project("Acme", settings)

    append_turn(project.id, "abc123", turn(), settings)
    append_turn(project.id, "abc123", turn("And the notice period?", "Thirty days (p. 4)."), settings)

    thread = read_thread(project.id, "abc123", settings)
    assert [item.question for item in thread.turns] == ["What is the cap?", "And the notice period?"]
    assert thread.turns[0].citations == ["contract.pdf p. 3"]


def test_each_turn_is_timestamped(s3, settings):
    project = create_project("Acme", settings)

    append_turn(project.id, "abc123", turn(), settings)

    assert read_thread(project.id, "abc123", settings).turns[0].asked_at


def test_two_reviews_in_one_project_keep_separate_threads(s3, settings):
    project = create_project("Acme", settings)

    append_turn(project.id, "aaa", turn("First?"), settings)
    append_turn(project.id, "bbb", turn("Second?"), settings)

    assert [item.question for item in read_thread(project.id, "aaa", settings).turns] == ["First?"]
    assert [item.question for item in read_thread(project.id, "bbb", settings).turns] == ["Second?"]


def test_an_unreadable_thread_starts_a_new_one_rather_than_failing(s3, settings):
    project = create_project("Acme", settings)
    s3.objects[(settings.s3_pdf_bucket, thread_key(project.id, "abc123"))] = b"{not json"

    assert read_thread(project.id, "abc123", settings).turns == []


@pytest.mark.parametrize("project_id", ["../escape", "with/slash", ""])
def test_a_project_id_that_could_escape_the_prefix_is_rejected(s3, settings, project_id):
    with pytest.raises(ValidationError):
        read_thread(project_id, "abc123", settings)


# --- what the prompt sees ------------------------------------------------


def test_an_empty_thread_renders_as_nothing():
    assert ChatThread(task_id="abc123", project_id="acme").render() == ""


def test_the_rendered_thread_holds_the_recent_turns():
    thread = ChatThread(task_id="abc123", project_id="acme", turns=[turn(f"Q{i}", f"A{i}") for i in range(10)])

    rendered = thread.render(limit=3)

    assert "Q9" in rendered and "Q7" in rendered
    assert "Q6" not in rendered


def test_a_spoken_question_is_marked_as_one():
    """Worth knowing when a transcription turns out to have misheard something."""
    spoken = ChatTurn(question="What is the cap?", answer="...", spoken=True)

    assert ChatTurn.from_dict(spoken.to_dict()).spoken is True
