"""Choosing the pages a question is about. No model and no index: term overlap
over the page markers the batching writes."""

from utils.batching import split_pages_into_batches
from utils.retrieval import DEFAULT_BUDGET, render_pages, score_page, select_pages, terms

PAGES = [
    "This Agreement is made between Acme Ltd and Beta LLC on 1 January 2026.",
    "The Supplier shall indemnify the Customer against all third party losses.",
    "Liability under this Agreement is capped at twelve months of fees.",
    "Either party may terminate on thirty days written notice.",
    "Notices shall be sent to the addresses in Schedule 2.",
]

CONTRACT = split_pages_into_batches(PAGES, len(PAGES))[0].markdown
DOCUMENTS = {"contract-a1b2c3d4.pdf": CONTRACT}


# --- terms ---------------------------------------------------------------


def test_terms_drop_words_that_match_everything():
    assert "the" not in terms("The liability of the supplier")
    assert "liability" in terms("The liability of the supplier")


def test_terms_normalize_case_and_punctuation():
    assert terms("**Liability!**") == ["liability"]


# --- scoring -------------------------------------------------------------


def test_a_page_using_more_of_the_question_scores_higher():
    question = set(terms("liability cap fees"))

    close = score_page(terms("Liability is capped at twelve months of fees."), question)
    far = score_page(terms("Notices go to Schedule 2."), question)

    assert close > far


def test_a_page_sharing_nothing_scores_zero():
    assert score_page(terms("Notices go to Schedule 2."), set(terms("indemnity"))) == 0.0


def test_a_long_page_does_not_win_by_length_alone():
    """Otherwise the longest page would answer every question."""
    question = set(terms("liability cap"))

    padded = score_page(terms("liability " + "filler " * 500), question)
    exact = score_page(terms("The liability cap is twelve months."), question)

    assert exact > padded


# --- select_pages --------------------------------------------------------


def test_the_matching_page_is_chosen():
    [page] = select_pages(DOCUMENTS, "What is the liability cap?")

    assert page.number == 3
    assert "capped at twelve months" in page.text


def test_the_best_page_is_the_one_that_answers():
    """Other pages may share a word; the one using most of the question wins."""
    pages = select_pages(DOCUMENTS, "When can either party terminate?")

    assert max(pages, key=lambda page: page.score).number == 4


def test_a_question_matching_nothing_falls_back_to_the_documents():
    """A question worded nothing like the contract still deserves the contract."""
    pages = select_pages(DOCUMENTS, "zebra quantum harpsichord")

    assert [page.number for page in pages] == [1, 2, 3, 4, 5]
    assert all(page.fallback for page in pages)


def test_the_fallback_shares_the_budget_between_documents():
    """A long contract must not crowd out the short one beside it."""
    long_document = split_pages_into_batches([f"Clause {i}. " + "filler " * 60 for i in range(1, 12)], 11)[0].markdown
    short = split_pages_into_batches(["The Landlord shall repair the roof."], 1)[0].markdown

    pages = select_pages({"long.pdf": long_document, "short.pdf": short}, "zebra quantum", budget=2000)

    assert {page.pdf_key for page in pages} == {"long.pdf", "short.pdf"}
    assert sum(len(page.text) for page in pages if page.pdf_key == "long.pdf") <= 2000


def test_the_fallback_starts_at_the_beginning_of_each_document():
    """Parties, term and fees live at the front of a contract."""
    pages = select_pages(DOCUMENTS, "zebra quantum", budget=120)

    assert pages[0].number == 1


def test_one_matching_page_is_not_topped_up_with_the_rest():
    """A question that matched gets what it matched, and a tight citation."""
    pages = select_pages(DOCUMENTS, "What is the liability cap?")

    assert [page.number for page in pages] == [3]
    assert not any(page.fallback for page in pages)


def test_documents_with_no_readable_text_select_nothing():
    assert select_pages({"a.pdf": "", "b.pdf": "   "}, "anything") == []


def test_pages_come_back_in_reading_order():
    pages = select_pages(DOCUMENTS, "agreement notices liability terminate indemnify")

    assert [page.number for page in pages] == sorted(page.number for page in pages)


def test_several_documents_are_searched_and_labelled():
    other = split_pages_into_batches(["The Landlord shall repair the roof."], 1)[0].markdown

    pages = select_pages({**DOCUMENTS, "lease-9f8e7d6c.pdf": other}, "who repairs the roof")

    assert [page.pdf_key for page in pages] == ["lease-9f8e7d6c.pdf"]


def test_the_budget_is_honoured():
    pages = select_pages(DOCUMENTS, "agreement notices liability terminate indemnify", budget=80)

    assert sum(len(page.text) for page in pages) <= 80 + max(len(page.text) for page in pages)
    assert len(pages) < len(PAGES)


def test_one_page_is_kept_even_when_it_alone_exceeds_the_budget():
    """An answer from one long page beats an answer from nothing."""
    pages = select_pages(DOCUMENTS, "liability cap", budget=5)

    assert len(pages) == 1


def test_the_same_question_always_chooses_the_same_pages():
    first = select_pages(DOCUMENTS, "liability and notices", DEFAULT_BUDGET)
    second = select_pages(DOCUMENTS, "liability and notices", DEFAULT_BUDGET)

    assert [(page.pdf_key, page.number) for page in first] == [(page.pdf_key, page.number) for page in second]


def test_blank_pages_are_skipped():
    markdown = split_pages_into_batches(["", "Liability is capped."], 2)[0].markdown

    assert [page.number for page in select_pages({"a.pdf": markdown}, "liability")] == [2]


# --- render_pages --------------------------------------------------------


def test_rendered_pages_name_their_document_and_page():
    rendered = render_pages(select_pages(DOCUMENTS, "liability cap"))

    assert "contract-a1b2c3d4.pdf, page 3" in rendered
    assert "capped at twelve months" in rendered


def test_rendering_nothing_says_so():
    assert "not available" in render_pages([])


def test_the_model_is_told_when_it_is_reading_the_whole_document():
    """Otherwise it reads a fallback as though someone chose those pages."""
    rendered = render_pages(select_pages(DOCUMENTS, "zebra quantum harpsichord"))

    assert "No passage matched" in rendered
    assert "This Agreement is made between Acme" in rendered


def test_matched_pages_are_not_labelled_as_a_fallback():
    assert "No passage matched" not in render_pages(select_pages(DOCUMENTS, "liability cap"))
