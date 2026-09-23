"""
Choosing which pages of a document a question is about.

A contract is far longer than a prompt, so a question cannot carry the whole
thing. This scores each page against the question by term overlap and takes the
best ones that fit in a character budget.

Deliberately not embeddings: no index to build, nothing to keep in sync with
the bucket, no extra dependency, and the result is inspectable — you can see
why a page was chosen. It rides on the `<!-- page N -->` markers the batching
already writes, so the answer can cite a real page number.
"""

import re
from dataclasses import dataclass

from utils.batching import iter_batch_pages
from utils.evidence import normalize

# a page of Markdown is roughly 2-3k characters, so this is a handful of pages
DEFAULT_BUDGET = 12000

# words that match everything and so distinguish nothing
STOP_WORDS = frozenset("""a an the and or but if is are was were be been being of in on at to from by for with without
    this that these those it its as we you they i do does did what which who whom whose when where
    why how can could shall should will would may might must there their them than then about into
    over under any all some each our your my me not no nor so such only own same too very s t just
    don now here he she him her his hers""".split())

WORD = re.compile(r"[a-z0-9']+")


@dataclass
class Page:
    """One page of a document, and why it was chosen."""

    pdf_key: str
    number: int
    text: str
    score: float = 0.0
    # true when the page is here because nothing matched, not because it did
    fallback: bool = False


def terms(text: str) -> list[str]:
    """The words worth matching on, in order."""
    return [word for word in WORD.findall(normalize(text)) if word not in STOP_WORDS and len(word) > 1]


def score_page(page_terms: list[str], question_terms: set[str]) -> float:
    """
    How well a page answers a question.

    The share of the question's words the page uses, so a long page cannot win
    simply by being long, with a small bonus for using several of them rather
    than one word repeatedly.
    """
    if not question_terms or not page_terms:
        return 0.0

    present = question_terms & set(page_terms)
    if not present:
        return 0.0

    hits = sum(1 for term in page_terms if term in question_terms)

    return len(present) / len(question_terms) + min(hits, 20) / 1000


def select_pages(documents: dict[str, str], question: str, budget: int = DEFAULT_BUDGET) -> list[Page]:
    """
    The pages of `{pdf_key: markdown}` most likely to answer `question`.

    Returns them in reading order — document, then page — rather than by score,
    because that is how the model should read them.

    When nothing matches, the documents themselves are sent instead: a question
    worded nothing like the contract ("is this worth signing?") would otherwise
    be answered from the summary alone, which is thinner than the document the
    reader is asking about.
    """
    question_terms = set(terms(question))

    scored = []
    for pdf_key, markdown in documents.items():
        for number, text in iter_batch_pages(markdown):
            if not text.strip():
                continue
            score = score_page(terms(text), question_terms)
            if score > 0:
                scored.append(Page(pdf_key=pdf_key, number=number, text=text, score=score))

    if not scored:
        return opening_pages(documents, budget)

    # highest score first, then reading order, so ties are broken the same way
    # every time rather than by dict iteration
    scored.sort(key=lambda page: (-page.score, page.pdf_key, page.number))

    chosen, used = [], 0
    for page in scored:
        if used + len(page.text) > budget and chosen:
            break
        chosen.append(page)
        used += len(page.text)

    return sorted(chosen, key=lambda page: (page.pdf_key, page.number))


def opening_pages(documents: dict[str, str], budget: int = DEFAULT_BUDGET) -> list[Page]:
    """
    As much of every document as fits, in reading order.

    The budget is shared between the documents rather than spent on whichever
    happens to be first, so a long contract cannot crowd out the short one
    beside it. Within a document the pages are taken from the beginning: a
    contract's parties, term and fees are near the front, and reading is where
    the model starts anyway.
    """
    readable = {key: pages for key, markdown in documents.items() if (pages := _pages_of(markdown))}

    if not readable:
        return []

    share = max(1, budget // len(readable))
    chosen = []

    for pdf_key, pages in readable.items():
        used = 0
        for number, text in pages:
            if used + len(text) > share and used:
                break
            chosen.append(Page(pdf_key=pdf_key, number=number, text=text, fallback=True))
            used += len(text)

    return chosen


def _pages_of(markdown: str) -> list[tuple[int, str]]:
    return [(number, text) for number, text in iter_batch_pages(markdown) if text.strip()]


def render_pages(pages: list[Page]) -> str:
    """The chosen pages as the prompt shows them, each labelled with its source."""
    if not pages:
        return "(the documents' text is not available; answer from the review above)"

    opening = ""
    if all(page.fallback for page in pages):
        # the model should know it is reading the documents rather than the
        # passages someone picked out as relevant
        opening = "No passage matched the question, so these are the documents themselves, from the beginning:\n\n"

    return opening + "\n\n".join(f"--- {page.pdf_key}, page {page.number} ---\n{page.text}" for page in pages)
