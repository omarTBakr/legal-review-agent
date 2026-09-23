"""Layer 1a: parsing an extraction reply and counting it up."""

from evaluation.layer1_metrics.extraction import ExtractionAttempt, parse_extraction, score_extraction


def attempt(gold=(), predicted=(), abstained=False, error="", category="Uncapped Liability") -> ExtractionAttempt:
    return ExtractionAttempt(
        document="ACME",
        category=category,
        gold_spans=tuple(gold),
        predicted_spans=tuple(predicted),
        abstained=abstained,
        error=error,
    )


def test_spans_are_cleaned_and_blanks_dropped():
    spans, abstained = parse_extraction({"spans": ["  liability is unlimited  ", "", "   "]})

    assert spans == ("liability is unlimited",)
    assert abstained is False


def test_a_lone_string_is_accepted_as_one_span():
    assert parse_extraction({"spans": "liability is unlimited"})[0] == ("liability is unlimited",)


def test_a_missing_or_wrongly_typed_spans_key_is_no_spans():
    assert parse_extraction({})[0] == ()
    assert parse_extraction({"spans": {"a": 1}})[0] == ()


def test_abstaining_while_listing_spans_is_not_an_abstention():
    """The spans are the claim; the flag must not buy credit for both answers."""
    spans, abstained = parse_extraction({"spans": ["liability is unlimited"], "abstain": True})

    assert spans == ("liability is unlimited",)
    assert abstained is False


def test_an_empty_list_with_the_flag_set_is_an_abstention():
    assert parse_extraction({"spans": [], "abstain": True}) == ((), True)


def test_a_correct_abstention_counts_towards_nothing_else():
    score = score_extraction([attempt(gold=(), predicted=())])

    assert score.correct_abstentions == 1
    assert score.overall.to_dict()["true_positives"] == 0
    assert score.overall.to_dict()["false_positives"] == 0


def test_answering_an_absent_category_is_a_false_positive_per_span():
    score = score_extraction([attempt(gold=(), predicted=("something", "something else"))])

    assert score.missed_abstentions == 1
    assert score.overall.false_positives == 2
    assert score.overall.precision == 0.0


def test_a_matching_span_is_a_true_positive():
    score = score_extraction([attempt(gold=("liability is unlimited",), predicted=("liability is unlimited",))])

    assert score.overall.true_positives == 1
    assert score.overall.f1 == 1.0


def test_a_missed_span_is_a_false_negative_and_a_wrong_one_is_both():
    score = score_extraction([attempt(gold=("liability is unlimited",), predicted=("the term is thirty-six months",))])

    assert score.overall.true_positives == 0
    assert score.overall.false_positives == 1
    assert score.overall.false_negatives == 1


def test_errors_are_counted_and_scored_against_nothing():
    """One unparseable reply should cost its own question, not distort the counts."""
    score = score_extraction([attempt(gold=("liability is unlimited",), error="LLMResponseError: nope")])

    assert score.errors == 1
    assert score.per_category == {}
    assert score.overall.false_negatives == 0


def test_categories_are_reported_separately_and_summed_into_overall():
    score = score_extraction(
        [
            attempt(category="Uncapped Liability", gold=("liability is unlimited",), predicted=("liability is unlimited",)),
            attempt(category="Non-Compete", gold=("shall not compete",), predicted=()),
        ]
    )

    assert score.per_category["Uncapped Liability"].recall == 1.0
    assert score.per_category["Non-Compete"].recall == 0.0
    assert score.overall.true_positives == 1
    assert score.overall.false_negatives == 1


def test_the_scorecard_says_this_is_not_the_papers_aupr():
    """The one number most likely to be quoted out of context carries its caveat."""
    assert "AUPR" in score_extraction([]).to_dict()["note"]


def test_an_attempt_survives_a_round_trip_through_the_results_file():
    original = attempt(gold=("a b c d",), predicted=("a b c e",))
    restored = ExtractionAttempt.from_dict(original.to_dict())

    assert restored == original
