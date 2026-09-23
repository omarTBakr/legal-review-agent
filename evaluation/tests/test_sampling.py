"""Sampling: the same seed must give the same 25 contracts, spread across strata."""

from evaluation.cuad.loader import Clause, Contract
from evaluation.cuad.sampling import assign_strata, stratified_sample


def corpus(size: int = 90) -> list[Contract]:
    """
    A synthetic corpus whose length and risk count both vary.

    Lengths cycle through three magnitudes and risk counts through 0, 1 and 2, so
    every stratum is populated and a draw that ignored the strata would show.
    """
    contracts = []

    for index in range(size):
        risks = index % 3
        spans = tuple(f"risky span {number} of contract {index}" for number in range(risks))
        contracts.append(
            Contract(
                title=f"CONTRACT-{index:03d}",
                text="x" * (1000 * (index % 3 + 1) ** 3),
                clauses=(
                    Clause(category="Uncapped Liability", definition="", spans=spans[:1]),
                    Clause(category="Non-Compete", definition="", spans=spans[1:2]),
                    Clause(category="Parties", definition="", spans=("Acme and Buyer",)),
                ),
            )
        )

    return contracts


def test_the_same_seed_gives_the_same_sample(categories):
    """A benchmark whose sample moves cannot be compared to last week's."""
    first = stratified_sample(corpus(), 25, seed=20260101, categories=categories)
    second = stratified_sample(corpus(), 25, seed=20260101, categories=categories)

    assert [contract.title for contract in first] == [contract.title for contract in second]


def test_a_different_seed_gives_a_different_sample(categories):
    first = stratified_sample(corpus(), 25, seed=1, categories=categories)
    second = stratified_sample(corpus(), 25, seed=2, categories=categories)

    assert [contract.title for contract in first] != [contract.title for contract in second]


def test_the_sample_is_the_size_asked_for(categories):
    assert len(stratified_sample(corpus(), 25, seed=1, categories=categories)) == 25


def test_asking_for_more_than_there_is_returns_everything(categories):
    contracts = corpus(10)

    assert stratified_sample(contracts, 25, seed=1, categories=categories) == contracts


def test_the_sample_is_returned_in_corpus_order(categories):
    sample = stratified_sample(corpus(), 25, seed=1, categories=categories)

    assert [contract.title for contract in sample] == sorted(contract.title for contract in sample)


def test_the_order_of_the_corpus_does_not_change_which_contracts_are_drawn(categories):
    """Shuffling CUAD before sampling must not be able to move the sample."""
    contracts = corpus()
    reversed_corpus = list(reversed(contracts))

    forwards = stratified_sample(contracts, 25, seed=7, categories=categories)
    backwards = stratified_sample(reversed_corpus, 25, seed=7, categories=categories)

    assert {contract.title for contract in forwards} == {contract.title for contract in backwards}


def test_a_short_sample_still_reaches_the_long_contracts(categories):
    """Round-robin across strata is the point: a uniform draw would miss these."""
    contracts = corpus()
    strata = assign_strata(contracts, categories)
    sample = stratified_sample(contracts, 9, seed=1, categories=categories)

    lengths = {strata[contract.title].length for contract in sample}

    assert lengths == {0, 1, 2}


def test_a_short_sample_reaches_contracts_with_several_risks(categories):
    contracts = corpus()
    strata = assign_strata(contracts, categories)
    sample = stratified_sample(contracts, 9, seed=1, categories=categories)

    assert len({strata[contract.title].risk_count for contract in sample}) > 1


def test_strata_are_derived_from_the_corpus_not_from_constants(categories):
    """Filtering CUAD before sampling must keep the strata balanced."""
    small = [contract for contract in corpus() if len(contract.text) <= 8000]
    strata = assign_strata(small, categories)

    assert len({stratum.length for stratum in strata.values()}) > 1
