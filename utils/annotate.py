"""
The original PDF with every risk highlighted where it was found.

A JSON list of risks is something to read; a marked-up contract is something to
work from. Each verified quote is highlighted in yellow — the same colour the
chat uses to follow the voice — with the model's description attached as a
popup note, so hovering a highlight in any PDF reader says what is wrong with it.

Only **verified** quotes are highlighted, and not as a policy decision: an
unverified quote is one the evidence check could not find in the document, so
there is nothing to draw a box around. Those risks are listed on an appendix
page at the end rather than dropped, because a risk missing from the marked-up
copy is a risk nobody reads.

pymupdf is already a dependency — it is what parsed the document in the first
place — so this adds no new library, and it searches the same PDF the review
read rather than a re-rendering of it.
"""

import pymupdf

from enums.RiskSeverity import RiskSeverity
from schemas.key_risk import KeyRisk
from utils.logger import get_logger

logger = get_logger(__name__)

# a highlight needs a colour per severity or the worst clause looks like the
# mildest; RGB floats, which is what pymupdf wants
COLOURS = {
    RiskSeverity.CRITICAL: (0.98, 0.80, 0.78),
    RiskSeverity.HIGH: (0.99, 0.88, 0.74),
    RiskSeverity.MEDIUM: (1.0, 0.95, 0.66),
    RiskSeverity.LOW: (0.88, 0.93, 0.99),
}

# search_for works on a line or two; a whole clause quoted across a page break
# will not be found as one string, so a long quote is looked for in pieces
CHUNK_WORDS = 12
MIN_CHUNK_WORDS = 6


def _chunks(quote: str) -> list[str]:
    """
    A quote as searchable pieces, longest first.

    The whole thing first, because one highlight over the real sentence beats
    several over fragments of it. The fallback pieces overlap nothing and are
    kept long enough that they cannot match some unrelated boilerplate.
    """
    words = quote.split()
    pieces = [quote]

    if len(words) > CHUNK_WORDS:
        # overlapping windows: a piece that straddles a line break is not found,
        # and with abutting windows its words are in no other piece either, so
        # the highlight comes back with holes in the middle of the sentence
        pieces += [
            " ".join(words[index : index + CHUNK_WORDS])
            for index in range(0, len(words), CHUNK_WORDS // 2)
            if len(words[index : index + CHUNK_WORDS]) >= MIN_CHUNK_WORDS
        ]

    return pieces


def find_quote(document: pymupdf.Document, quote: str, page_hint: int | None = None) -> tuple[int, list]:
    """
    Where a quote is in the document: (page number, rectangles).

    The page the review recorded is tried first and the rest only if it is not
    there — a quote can be reported on the wrong page, and the highlight belongs
    where the text actually is. Returns (0, []) when nothing was found.

    A quote long enough to wrap is not found as one string, so the fallback
    collects **every** piece that matches on a page rather than the first.
    Highlighting only the first piece marks the opening of a clause and leaves
    the half that says "and is not capped" unmarked, which reads as though the
    review cared about the wrong part of the sentence.
    """
    order = list(range(len(document)))

    if page_hint and 1 <= page_hint <= len(document):
        order.remove(page_hint - 1)
        order.insert(0, page_hint - 1)

    # the whole thing, if any page has it: one clean highlight over the sentence
    for index in order:
        found = document[index].search_for(quote)
        if found:
            return index + 1, found

    pieces = _chunks(quote)[1:]
    if not pieces:
        return 0, []

    best_page, best_rectangles = 0, []

    for index in order:
        page = document[index]
        rectangles = [rectangle for piece in pieces for rectangle in page.search_for(piece)]

        # the page carrying most of the quote, so a clause that happens to share
        # a phrase with a later page is not split across both
        if len(rectangles) > len(best_rectangles):
            best_page, best_rectangles = index + 1, rectangles

    return best_page, best_rectangles


def _note(risk: KeyRisk) -> str:
    """What the popup says when someone clicks the highlight."""
    lines = [f"{risk.severity.value.upper()}: {risk.description}"]

    if risk.location:
        lines.append(f"Where: {risk.location}")

    # carried through when suggested redlines exist; harmless until they do
    suggestion = getattr(risk, "suggestion", "")
    if suggestion:
        lines.append(f"Suggested wording: {suggestion}")

    return "\n\n".join(lines)


def annotate(pdf: bytes, risks: list[KeyRisk], title: str = "") -> tuple[bytes, dict]:
    """
    Marks up a PDF with its risks and returns (annotated bytes, what happened).

    The report says how many risks were highlighted and how many could not be
    placed, so a caller can tell the difference between "this contract is clean"
    and "none of the quotes could be found".
    """
    document = pymupdf.open(stream=pdf, filetype="pdf")
    highlighted, unplaced = 0, []

    try:
        for risk in risks:
            if not risk.quote or not risk.quote_verified:
                unplaced.append(risk)
                continue

            page_number, rectangles = find_quote(document, risk.quote, risk.page)
            if not rectangles:
                # verified against the Markdown, but pymupdf cannot find it in
                # the page text: a table, a ligature, a column break
                unplaced.append(risk)
                continue

            page = document[page_number - 1]
            highlight = page.add_highlight_annot(rectangles)
            highlight.set_colors(stroke=COLOURS.get(risk.severity, COLOURS[RiskSeverity.LOW]))
            highlight.set_info(title=title or "Legal review", content=_note(risk))
            highlight.update()

            highlighted += 1

        if unplaced:
            _append_unplaced(document, unplaced)

        annotated = document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()

    report = {
        "risks": len(risks),
        "highlighted": highlighted,
        "listed_only": len(unplaced),
    }
    logger.info("annotated %s: %d highlighted, %d listed only", title or "a document", highlighted, len(unplaced))

    return annotated, report


def _append_unplaced(document: pymupdf.Document, risks: list[KeyRisk]) -> None:
    """
    A page at the end for the risks that could not be highlighted.

    Nothing is dropped: a risk missing from the marked-up copy is a risk nobody
    reads, and "we could not point at this one" is information in itself.
    """
    page = document.new_page()
    writer = pymupdf.TextWriter(page.rect)

    # pymupdf's own base-14 codes: "hebo" is Helvetica-Bold. "helvB" is not a
    # name it knows, and asking for one it does not raises rather than falling back
    heading = pymupdf.Font("hebo")
    body = pymupdf.Font("helv")

    point = pymupdf.Point(56, 72)
    writer.append(point, "Risks that could not be located in the text", font=heading, fontsize=13)

    point.y += 20
    writer.append(
        point,
        "The quote for each of these could not be found on the page, so there is nothing to highlight.",
        font=body,
        fontsize=8.5,
    )

    point.y += 24

    for risk in risks:
        for line in _wrap(f"[{risk.severity.value.upper()}] {risk.description}", 92):
            writer.append(point, line, font=body, fontsize=9.5)
            point.y += 14

        if risk.quote:
            for line in _wrap(f"Quote: {risk.quote}", 96):
                writer.append(point, line, font=body, fontsize=8.5)
                point.y += 12

        point.y += 8

        # a very long list needs another page rather than running off this one
        if point.y > page.rect.height - 72:
            writer.write_text(page)
            page = document.new_page()
            writer = pymupdf.TextWriter(page.rect)
            point = pymupdf.Point(56, 72)

    writer.write_text(page)


def _wrap(text: str, width: int) -> list[str]:
    """Greedy wrapping, so a long description does not run off the page."""
    lines, current = [], ""

    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate

    if current:
        lines.append(current)

    return lines
