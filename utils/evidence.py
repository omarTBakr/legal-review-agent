"""
Checks that the passage a risk quotes is really in the document.

The model is asked to copy a short passage for every risk. Nothing it says
about that passage is trusted: a quote is verified only when it can be found
in the batch it was drawn from, and the page it was found on replaces whatever
page the model claimed. A quote that cannot be found is kept but left
unverified, so the reviewer sees the risk and knows to check it by hand.
"""

import re
from dataclasses import replace
from difflib import SequenceMatcher

from schemas.key_risk import KeyRisk
from utils.batching import PAGE_MARKER_PATTERN, iter_batch_pages

# shorter than this, a quote matches almost anywhere and proves nothing
MIN_QUOTE_CHARACTERS = 15

# share of a quote that has to line up with the page for a fuzzy match
MIN_FUZZY_COVERAGE = 0.9

_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)
_MARKDOWN_NOISE = re.compile(r"[*_#|>`]")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """
    Reduces text to what a quote and the page it came from should share.

    Case, whitespace, curly quotes, dashes and the Markdown pymupdf4llm adds
    (bold, headings, table pipes) all differ between the parsed page and what
    the model writes back, without the words themselves being different.
    """
    text = PAGE_MARKER_PATTERN.sub(" ", text)
    text = text.translate(_TRANSLATE)
    text = _MARKDOWN_NOISE.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip().lower()


def _fuzzy_contains(page: str, quote: str) -> bool:
    """
    Whether nearly all of `quote` lines up with one stretch of `page`.

    Anchors on the longest exact run the two share, then measures how much of
    the quote matches the stretch of page around that run. Matching against
    the whole page instead would let common words scattered across it add up
    to a false match.
    """
    matcher = SequenceMatcher(None, page, quote, autojunk=False)
    anchor = matcher.find_longest_match(0, len(page), 0, len(quote))
    if anchor.size == 0:
        return False

    slack = len(quote) // 10 + 1
    start = max(0, anchor.a - anchor.b - slack)
    end = min(len(page), anchor.a + (len(quote) - anchor.b) + slack)

    blocks = SequenceMatcher(None, page[start:end], quote, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(quote) >= MIN_FUZZY_COVERAGE


def find_quote(quote: str, markdown: str) -> int | None:
    """
    The page of `markdown` that `quote` appears on, or None.

    An exact match (after normalizing) on any page wins; only when there is
    none does a fuzzy match get a chance, so a near-duplicate passage on an
    earlier page cannot claim a quote that appears word for word later.
    """
    needle = normalize(quote)
    if len(needle) < MIN_QUOTE_CHARACTERS:
        return None

    pages = [(number, normalize(text)) for number, text in iter_batch_pages(markdown)]

    for number, text in pages:
        if needle in text:
            return number

    for number, text in pages:
        if _fuzzy_contains(text, needle):
            return number

    return None


def verify_risks(risks: list[KeyRisk], markdown: str) -> list[KeyRisk]:
    """
    Checks each risk's quote against the batch it came from.

    A quote that is found is marked verified and takes the page it was found
    on. One that is not keeps the model's page but stays unverified.
    """
    verified = []

    for risk in risks:
        page = find_quote(risk.quote, markdown)
        if page is None:
            verified.append(replace(risk, quote_verified=False))
        else:
            verified.append(replace(risk, quote_verified=True, page=page or risk.page))

    return verified


def carry_verification(new_risks: list[KeyRisk], known_risks: list[KeyRisk]) -> list[KeyRisk]:
    """
    Carries verification through a step that never sees the document.

    The merge and the human follow-up rewrite risks from earlier advice rather
    than from the pages themselves. A rewritten risk stays verified only when
    its quote is one that was already verified, or a part of one, and it takes
    that quote's page. A quote the model changed along the way loses its
    verification.
    """
    known = [(normalize(risk.quote), risk.page) for risk in known_risks if risk.quote_verified]

    carried = []

    for risk in new_risks:
        needle = normalize(risk.quote)
        match = None
        if len(needle) >= MIN_QUOTE_CHARACTERS:
            match = next(((quote, page) for quote, page in known if needle in quote), None)

        if match is None:
            carried.append(replace(risk, quote_verified=False))
        else:
            carried.append(replace(risk, quote_verified=True, page=match[1] or risk.page))

    return carried
