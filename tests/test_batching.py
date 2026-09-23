import pytest

from utils.batching import iter_batch_pages, split_pages_into_batches

PAGES = [f"body {i}" for i in range(1, 8)]


def test_pages_are_grouped():
    assert len(split_pages_into_batches(PAGES, 3)) == 3


def test_the_last_batch_holds_the_remainder():
    assert split_pages_into_batches(PAGES, 3)[-1].markdown == "<!-- page 7 -->\n\nbody 7"


def test_page_numbers_are_one_based_and_contiguous():
    """A batch has to be able to say where in the document it came from."""
    batches = split_pages_into_batches(PAGES, 3)

    assert (batches[0].first_page, batches[0].last_page) == (1, 3)
    assert (batches[1].first_page, batches[1].last_page) == (4, 6)
    assert (batches[2].first_page, batches[2].last_page) == (7, 7)


def test_indexes_are_sequential():
    assert [b.index for b in split_pages_into_batches(PAGES, 2)] == [0, 1, 2, 3]


def test_every_page_appears_exactly_once():
    joined = " ".join(b.markdown for b in split_pages_into_batches(PAGES, 3))

    for page in PAGES:
        assert joined.count(page) == 1


def test_a_single_batch_when_the_document_is_short():
    batches = split_pages_into_batches(PAGES, 100)

    assert len(batches) == 1
    assert (batches[0].first_page, batches[0].last_page) == (1, 7)


def test_one_page_per_batch():
    assert len(split_pages_into_batches(PAGES, 1)) == 7


def test_no_pages_means_no_batches():
    assert split_pages_into_batches([], 3) == []


def test_blank_pages_are_kept_so_numbering_stays_true():
    """Dropping an empty page would make every later page number wrong."""
    batches = split_pages_into_batches(["a", "", "c"], 1)

    assert [b.first_page for b in batches] == [1, 2, 3]


@pytest.mark.parametrize("size", [0, -1])
def test_a_nonsense_batch_size_is_rejected(size):
    with pytest.raises(ValueError, match="at least 1"):
        split_pages_into_batches(PAGES, size)


def test_the_label_reads_naturally():
    batches = split_pages_into_batches(PAGES, 3)

    assert batches[0].label == "pages 1-3"
    assert batches[2].label == "page 7"


def test_every_page_opens_with_its_marker():
    """The model cites pages, and the evidence check splits batches, by these markers."""
    batch = split_pages_into_batches(PAGES, 3)[1]

    assert batch.markdown.startswith("<!-- page 4 -->")
    assert "<!-- page 5 -->" in batch.markdown and "<!-- page 6 -->" in batch.markdown


def test_a_blank_page_still_gets_its_marker():
    batch = split_pages_into_batches(["a", "", "c"], 3)[0]

    assert [number for number, _ in iter_batch_pages(batch.markdown)] == [1, 2, 3]


def test_batch_pages_round_trip():
    batches = split_pages_into_batches(PAGES, 3)

    pages = [page for batch in batches for page in iter_batch_pages(batch.markdown)]

    assert pages == [(i, f"body {i}") for i in range(1, 8)]


def test_text_without_markers_is_page_zero_rather_than_lost():
    assert iter_batch_pages("no markers here") == [(0, "no markers here")]
