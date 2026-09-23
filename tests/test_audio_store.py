"""The recordings, kept in the bucket beside the thread they belong to."""

import pytest

from exceptions.storage import ObjectNotFoundError, UploadError
from exceptions.validation import ValidationError
from utils.audio_store import ANSWER, QUESTION, audio_key, has_audio, read_audio, store_audio
from utils.projects import create_project

WAV = b"RIFF$\x00\x00\x00WAVEfmt fake"


def test_the_key_says_which_review_and_turn_it_belongs_to():
    key = audio_key("acme-b4b731b4", "abc123", 2, QUESTION)

    assert key == "acme-b4b731b4/chats/abc123/audio/2-question.wav"


def test_questions_and_answers_do_not_share_a_key():
    assert audio_key("acme-b4b731b4", "abc123", 0, QUESTION) != audio_key("acme-b4b731b4", "abc123", 0, ANSWER)


def test_only_questions_and_answers_have_audio():
    with pytest.raises(ValueError, match="question or an answer"):
        audio_key("acme-b4b731b4", "abc123", 0, "whistling")


@pytest.mark.parametrize("project_id", ["../escape", "with/slash", ""])
def test_a_project_id_that_could_escape_the_prefix_is_rejected(project_id):
    with pytest.raises(ValidationError):
        audio_key(project_id, "abc123", 0, QUESTION)


def test_a_clip_is_stored_and_read_back(s3, settings):
    project = create_project("Acme", settings)

    key = store_audio(project.id, "abc123", 0, QUESTION, WAV, settings)

    assert key == audio_key(project.id, "abc123", 0, QUESTION)
    assert read_audio(project.id, "abc123", 0, QUESTION, settings) == WAV


def test_nothing_is_stored_when_storing_audio_is_off(s3, settings, monkeypatch):
    """The transcript is still kept; only the recording is not."""
    monkeypatch.setattr(settings, "store_audio", False)
    project = create_project("Acme", settings)

    assert store_audio(project.id, "abc123", 0, QUESTION, WAV, settings) == ""
    assert not [key for bucket, key in s3.objects if "audio" in key]


def test_empty_audio_is_not_stored(s3, settings):
    project = create_project("Acme", settings)

    assert store_audio(project.id, "abc123", 0, QUESTION, b"", settings) == ""


def test_a_failure_to_store_does_not_take_the_question_down_with_it(s3, settings, monkeypatch):
    """The answer is already written; losing the recording is a footnote."""
    import utils.audio_store

    def refuse(*args, **kwargs):
        raise UploadError("bucket is read-only")

    monkeypatch.setattr(utils.audio_store, "upload_s3_file", refuse)
    project = create_project("Acme", settings)

    assert store_audio(project.id, "abc123", 0, ANSWER, WAV, settings) == ""


def test_reading_a_clip_that_was_never_kept_says_so(s3, settings):
    project = create_project("Acme", settings)

    with pytest.raises(ObjectNotFoundError):
        read_audio(project.id, "abc123", 0, ANSWER, settings)


def test_has_audio_answers_without_raising(s3, settings):
    project = create_project("Acme", settings)
    store_audio(project.id, "abc123", 1, ANSWER, WAV, settings)

    assert has_audio(project.id, "abc123", 1, ANSWER, settings)
    assert not has_audio(project.id, "abc123", 2, ANSWER, settings)
