from enums.PromptName import PromptName
from prompts.prompt import Prompt

SYSTEM = """You are consolidating several excerpt-level reviews of one document \
into a single review.

Merge duplicate risks, keeping the highest severity assigned to any of them and \
the most precise location, and keep the quote and page of the risk you keep. \
Copy every quote and page exactly as given: never rewrite, shorten or combine \
quotes, because each one is checked against the document. Keep every distinct \
risk: dropping one is worse than repeating yourself. The summary should describe the whole document, not the \
individual excerpts, and should not mention that it was reviewed in parts.

Reply with a single JSON object and nothing else, in the same shape as the \
inputs:

{
  "summary": "...",
  "key_risks": [
    {"description": "...", "severity": "...", "location": "...", "quote": "...", "page": 1}
  ],
  "needs_human": false,
  "question": ""
}

Set "needs_human" to true if any excerpt raised a question that still matters \
for the document as a whole, and carry that question through."""

USER_TEMPLATE = """Document: {pdf_key}

Excerpt reviews, in order:

{parts}

Consolidate them and reply with the JSON object."""

PROMPT = Prompt(name=PromptName.MERGE_ADVICE, system=SYSTEM, user_template=USER_TEMPLATE)
