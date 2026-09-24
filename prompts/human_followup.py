from enums.PromptName import PromptName
from prompts.prompt import Prompt

SYSTEM = """You are revising your own review of a document after a human \
answered the question you raised.

Treat the answer as authoritative: it comes from someone with access to facts \
the document does not contain. Revise the summary and the risks in light of it. \
A risk the answer resolves should be removed, not downgraded to a footnote; a \
risk the answer makes worse should have its severity raised. Keep each remaining risk's quote and page exactly as given: \
never rewrite a quote, because each one is checked against the document.

Reply with a single JSON object and nothing else, in the same shape as before. \
Keep confidence, category, recommended_action, quote and page for every \
remaining risk. \
Set "needs_human" to false and leave "question" empty: the question has been \
answered."""

USER_TEMPLATE = """Document: {pdf_key}

Your draft review:
{draft}

You asked: {question}
The answer: {answer}

Revise your review and reply with the JSON object."""

PROMPT = Prompt(name=PromptName.HUMAN_FOLLOWUP, system=SYSTEM, user_template=USER_TEMPLATE)
