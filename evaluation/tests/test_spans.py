"""The matching rule every other number rests on."""

import pytest

from evaluation.common.spans import Counts, covers_any, jaccard, match_spans, tokens


def test_identical_text_scores_one():
    assert jaccard("The Buyer's liability is unlimited.", "The Buyer's liability is unlimited.") == 1.0


def test_two_empty_spans_score_zero_rather_than_one():
    """An abstention must not be able to score a perfect match against another abstention."""
    assert jaccard("", "") == 0.0
    assert jaccard("liability", "") == 0.0


def test_markdown_and_curly_quotes_do_not_change_the_score():
    """The review quotes out of pymupdf4llm Markdown; CUAD's text is plain."""
    assert jaccard("**Liability** is “unlimited”", 'Liability is "unlimited"') == 1.0


def test_word_order_and_case_are_ignored_but_words_are_not():
    assert jaccard("unlimited liability", "LIABILITY UNLIMITED") == 1.0
    assert jaccard("unlimited liability", "capped liability") == pytest.approx(1 / 3)


def test_a_longer_quote_around_the_same_clause_still_matches():
    gold = "The Buyer's liability under this Agreement is unlimited."
    quote = "Notwithstanding the foregoing, the Buyer's liability under this Agreement is unlimited."

    assert jaccard(quote, gold) >= 0.5


def test_a_quote_that_only_shares_boilerplate_does_not_match():
    gold = "The Buyer's liability under this Agreement is unlimited."
    quote = "The Seller shall under this Agreement deliver the Goods."

    assert jaccard(quote, gold) < 0.5


def test_page_markers_are_stripped_before_comparing():
    assert "page" not in tokens("<!-- page 4 -->")


def test_matching_is_one_to_one_so_one_huge_quote_cannot_cover_everything():
    """A model quoting the whole contract must not be credited with every span."""
    gold = ["liability is unlimited", "the term is thirty-six months", "assignment requires consent"]
    everything = " ".join(gold)

    result = match_spans([everything], gold)

    assert result.true_positives <= 1
    assert len(result.unmatched_gold) >= 2


def test_matching_pairs_the_best_overlap_first_regardless_of_order():
    gold = ["liability is unlimited under this agreement", "the term is thirty-six months"]
    predicted = ["the term is thirty-six months", "liability is unlimited under this agreement"]

    result = match_spans(predicted, gold)

    assert {(match.predicted_index, match.gold_index) for match in result.matches} == {(0, 1), (1, 0)}
    assert result.unmatched_predicted == ()
    assert result.unmatched_gold == ()


def test_matching_is_deterministic_when_two_predictions_tie():
    gold = ["liability is unlimited"]
    predicted = ["liability is unlimited", "liability is unlimited"]

    first = match_spans(predicted, gold)
    second = match_spans(predicted, gold)

    assert first == second
    assert first.matches[0].predicted_index == 0


def test_covers_any_lets_one_span_be_covered_twice():
    """Recall asks whether a clause was surfaced, not how many risks surfaced it."""
    gold = ["liability is unlimited"]

    assert covers_any("liability is unlimited", gold) == 0
    assert covers_any("the liability is unlimited indeed", gold) == 0
    assert covers_any("the term is thirty-six months", gold) is None


def test_covers_any_picks_the_best_of_several_candidates():
    gold = ["liability is capped at ten percent", "liability is unlimited"]

    assert covers_any("liability is unlimited", gold) == 1


def test_counts_add_and_derive_the_usual_three():
    total = Counts(true_positives=3, false_positives=1) + Counts(true_positives=1, false_negatives=2)

    assert (total.true_positives, total.false_positives, total.false_negatives) == (4, 1, 2)
    assert total.precision == pytest.approx(4 / 5)
    assert total.recall == pytest.approx(4 / 6)
    assert total.f1 == pytest.approx(2 * (4 / 5) * (4 / 6) / ((4 / 5) + (4 / 6)))


def test_counts_with_nothing_in_them_are_zero_not_an_error():
    assert Counts().precision == 0.0
    assert Counts().recall == 0.0
    assert Counts().f1 == 0.0
