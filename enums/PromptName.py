from enum import Enum


class PromptName(Enum):
    """The prompts in prompts/, addressable by name."""

    LEGAL_ADVICE = "legal_advice"
    MERGE_ADVICE = "merge_advice"
    HUMAN_FOLLOWUP = "human_followup"
    REVIEW_CHAT = "review_chat"
