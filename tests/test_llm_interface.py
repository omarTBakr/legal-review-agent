"""parse_json_object is where a model reply stops being trusted."""

import pytest

from exceptions.llm import LLMResponseError
from interfaces.llm.interface import LLMInterface

parse = LLMInterface.parse_json_object


def test_plain_json():
    assert parse('{"summary": "ok"}') == {"summary": "ok"}


def test_a_fenced_block():
    assert parse('```json\n{"summary": "ok"}\n```') == {"summary": "ok"}


def test_a_fence_without_a_language():
    assert parse('```\n{"summary": "ok"}\n```') == {"summary": "ok"}


def test_leading_commentary():
    """Models like to introduce themselves before answering."""
    assert parse('Sure, here is the review:\n{"summary": "ok"}') == {"summary": "ok"}


def test_trailing_commentary():
    assert parse('{"summary": "ok"}\nLet me know if you need more.') == {"summary": "ok"}


def test_nested_objects_survive():
    parsed = parse('{"key_risks": [{"severity": "high"}]}')

    assert parsed["key_risks"][0]["severity"] == "high"


def test_an_array_of_objects_is_rejected_not_truncated():
    """Reducing a list to its first element would silently lose advice."""
    with pytest.raises(LLMResponseError, match="expected an object"):
        parse('[{"summary": "first"}, {"summary": "second"}]')


def test_a_json_string_is_rejected():
    with pytest.raises(LLMResponseError, match="expected an object"):
        parse('"just a string"')


@pytest.mark.parametrize("reply", ["", "   ", "no json here", "I cannot help with that."])
def test_no_object_at_all_is_rejected(reply):
    with pytest.raises(LLMResponseError, match="no JSON object"):
        parse(reply)


def test_an_unrepairable_reply_is_rejected():
    with pytest.raises(LLMResponseError, match="could not be repaired"):
        parse("Here you go: {")


# --- json_repair as a safeguard ------------------------------------------


def test_an_unterminated_string_is_repaired():
    assert parse('{"summary": "unterminated}') == {"summary": "unterminated"}


def test_a_trailing_comma_is_repaired():
    assert parse('{"summary": "ok", "needs_human": false,}') == {"summary": "ok", "needs_human": False}


def test_unquoted_keys_and_single_quotes_are_repaired():
    assert parse("{summary: 'ok'}") == {"summary": "ok"}


def test_a_missing_comma_is_repaired():
    assert parse('{"summary": "ok" "question": ""}') == {"summary": "ok", "question": ""}


def test_a_reply_cut_off_at_the_token_limit_keeps_what_it_had():
    """A truncated reply must keep its later fields, not be cut back to a nested object."""
    parsed = parse('{"key_risks": [{"description": "x", "severity": "high"}], "summary": "cut off')

    assert parsed == {"key_risks": [{"description": "x", "severity": "high"}], "summary": "cut off"}


def test_a_repaired_fenced_block():
    assert parse('```json\n{"summary": "ok",}\n```') == {"summary": "ok"}


def test_repair_does_not_turn_several_objects_into_one():
    """Stray braces after the object repair to a list, which is still rejected."""
    with pytest.raises(LLMResponseError, match="expected an object"):
        parse('{"summary": "ok",}\nHope that helps! {not json}')


def test_a_repair_is_logged(caplog):
    """A repaired reply is a sign the prompt or model needs attention."""
    with caplog.at_level("WARNING"):
        parse('{"summary": "ok",}', prompt_name="legal_advice")

    assert "legal_advice reply was malformed JSON" in caplog.text


def test_a_plain_array_is_rejected():
    """The callers all expect an object, not a list."""
    with pytest.raises(LLMResponseError, match="expected an object"):
        parse("[1, 2, 3]")


def test_the_prompt_name_is_in_the_message():
    """So a failure says which call went wrong."""
    with pytest.raises(LLMResponseError, match="legal_advice"):
        parse("nonsense", prompt_name="legal_advice")


async def test_complete_json_validates_what_complete_returned(llm):
    from enums.PromptName import PromptName
    from prompts import get_prompt

    llm.script(PromptName.LEGAL_ADVICE, "not json at all")

    with pytest.raises(LLMResponseError):
        await llm.complete_json(
            get_prompt(PromptName.LEGAL_ADVICE),
            pdf_key="a.pdf",
            batch_label="page 1",
            batch_number=1,
            batch_count=1,
            markdown="text",
        )
