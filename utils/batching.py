import re

from schemas.page_batch import PageBatch

PAGE_MARKER = "<!-- page {number} -->"
PAGE_MARKER_PATTERN = re.compile(r"<!-- page (\d+) -->")


def split_pages_into_batches(pages: list[str], pages_per_batch: int) -> list[PageBatch]:
    """
    Groups a document's pages into LLM-sized batches.

    Page numbers are 1-based and refer to the original document, so a batch can
    say where it came from. Blank pages are kept: dropping them would make the
    numbering lie.

    Every page opens with a `<!-- page N -->` marker, so the model can cite the
    page a risk is on and the evidence check can split a batch back into its
    pages without a second copy of the text travelling through the workflow.
    """
    if pages_per_batch < 1:
        raise ValueError(f"pages_per_batch must be at least 1, got {pages_per_batch}")

    batches = []

    for index, start in enumerate(range(0, len(pages), pages_per_batch)):
        chunk = pages[start : start + pages_per_batch]

        batches.append(
            PageBatch(
                index=index,
                first_page=start + 1,
                last_page=start + len(chunk),
                markdown="\n\n".join(
                    f"{PAGE_MARKER.format(number=start + offset + 1)}\n\n{page.strip()}".strip()
                    for offset, page in enumerate(chunk)
                ),
            )
        )

    return batches


def iter_batch_pages(markdown: str) -> list[tuple[int, str]]:
    """
    Splits a batch's Markdown back into (page number, text) pairs.

    Text before the first marker, or a batch with no markers at all, is
    reported as page 0 so that nothing is silently dropped.
    """
    parts = PAGE_MARKER_PATTERN.split(markdown)

    # re.split with one group yields [before, number, text, number, text, ...]
    pages = [(0, parts[0].strip())] if parts[0].strip() else []
    pages.extend((int(number), text.strip()) for number, text in zip(parts[1::2], parts[2::2], strict=True))

    return pages
