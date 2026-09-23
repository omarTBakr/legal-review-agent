import pytest

from enums.PromptName import PromptName
from prompts import get_prompt
from prompts.prompt import Prompt

ADVICE_VARS = {
    "pdf_key": "contract.pdf",
    "batch_label": "pages 1-2",
    "batch_number": 1,
    "batch_count": 3,
    "markdown": "Clause 1.",
}


@pytest.mark.parametrize("name", list(PromptName))
def test_every_name_resolves(name):
    assert isinstance(get_prompt(name), Prompt)


@pytest.mark.parametrize("name", list(PromptName))
def test_the_prompt_knows_its_own_name(name):
    assert get_prompt(name).name is name


@pytest.mark.parametrize("name", list(PromptName))
def test_every_prompt_has_a_system_and_a_template(name):
    prompt = get_prompt(name)

    assert prompt.system.strip()
    assert prompt.user_template.strip()


@pytest.mark.parametrize("name", [name for name in PromptName if get_prompt(name).expects_json])
def test_a_prompt_parsed_as_json_asks_for_json(name):
    """Those replies go through from_model, so they have to be JSON."""
    assert "json" in get_prompt(name).system.lower()


def test_the_chat_prompt_wants_prose():
    """Its answer is read aloud, so a JSON object would be absurd."""
    prompt = get_prompt(PromptName.REVIEW_CHAT)

    assert prompt.expects_json is False
    assert "no json" in prompt.system.lower()


def test_rendering_fills_the_template():
    rendered = get_prompt(PromptName.LEGAL_ADVICE).render(**ADVICE_VARS)

    assert "contract.pdf" in rendered
    assert "pages 1-2" in rendered
    assert "Clause 1." in rendered


def test_a_missing_variable_names_itself():
    """Better than sending a half-filled prompt to a paid API."""
    with pytest.raises(KeyError, match="batch_label"):
        get_prompt(PromptName.LEGAL_ADVICE).render(pdf_key="a.pdf")


def test_prompts_are_frozen():
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        get_prompt(PromptName.LEGAL_ADVICE).system = "something else"


def test_the_followup_prompt_tells_the_model_to_stop_asking():
    """Otherwise a document could bounce between the model and a human forever."""
    system = get_prompt(PromptName.HUMAN_FOLLOWUP).system.lower()

    assert "needs_human" in system and "false" in system


def test_an_unregistered_name_raises():
    with pytest.raises(KeyError):
        get_prompt("legal_advice")
