"""Prompt text, one file per prompt, addressable by PromptName.

    from prompts import get_prompt
    from enums.PromptName import PromptName

    prompt = get_prompt(PromptName.LEGAL_ADVICE)

Keeping the text here rather than inline in the activities means it can be read
and edited without touching the code that sends it.
"""

from enums.PromptName import PromptName
from prompts.human_followup import PROMPT as HUMAN_FOLLOWUP_PROMPT
from prompts.legal_advice import PROMPT as LEGAL_ADVICE_PROMPT
from prompts.merge_advice import PROMPT as MERGE_ADVICE_PROMPT
from prompts.prompt import Prompt
from prompts.review_chat import PROMPT as REVIEW_CHAT_PROMPT

_PROMPTS = {
    PromptName.LEGAL_ADVICE: LEGAL_ADVICE_PROMPT,
    PromptName.MERGE_ADVICE: MERGE_ADVICE_PROMPT,
    PromptName.HUMAN_FOLLOWUP: HUMAN_FOLLOWUP_PROMPT,
    PromptName.REVIEW_CHAT: REVIEW_CHAT_PROMPT,
}


def get_prompt(name: PromptName) -> Prompt:
    """Returns the prompt registered under `name`."""
    try:
        return _PROMPTS[name]
    except KeyError as exc:
        raise KeyError(f"no prompt registered for {name}") from exc


__all__ = ["Prompt", "get_prompt"]
