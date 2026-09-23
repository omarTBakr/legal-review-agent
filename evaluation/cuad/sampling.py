"""Choosing which 25 of the 510 contracts a run scores.

A uniform sample of CUAD is mostly medium-length contracts with two or three
annotated risk categories, which is the easy middle of the distribution. Two
axes decide the strata instead:

  * length, because a contract long enough to need several batches exercises the
    merge, and the merge is where quotes lose their verification;
  * how many risky categories are annotated, because a contract with one risk
    and a contract with nine are different tests of recall.

Terciles on each axis give nine strata, drawn round-robin so a short run still
touches the long contracts. Same corpus, same seed, same sample — the tests
assert that, because a benchmark whose sample moves cannot be compared to last
week's.
"""

import random
from dataclasses import dataclass

from evaluation.cuad.loader import Contract, RiskCategories


@dataclass(frozen=True)
class Stratum:
    """Which of the nine buckets a contract falls into."""

    length: int
    risk_count: int

    def __str__(self) -> str:
        return f"length={self.length} risks={self.risk_count}"


def _terciles(values: list[int]) -> tuple[int, int]:
    """The two cut points that split sorted `values` into three."""
    ordered = sorted(values)
    if not ordered:
        return 0, 0

    return ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]


def _bucket(value: int, cuts: tuple[int, int]) -> int:
    return 0 if value < cuts[0] else 1 if value < cuts[1] else 2


def assign_strata(contracts: list[Contract], categories: RiskCategories) -> dict[str, Stratum]:
    """
    The stratum of every contract, keyed by title.

    The cut points come from the corpus being sampled rather than from constants,
    so the strata stay balanced if CUAD is ever filtered before it gets here.
    """
    lengths = [len(contract.text) for contract in contracts]
    risk_counts = [len(contract.annotated_clauses(set(categories.risky))) for contract in contracts]

    length_cuts = _terciles(lengths)
    risk_cuts = _terciles(risk_counts)

    return {
        contract.title: Stratum(length=_bucket(length, length_cuts), risk_count=_bucket(risks, risk_cuts))
        for contract, length, risks in zip(contracts, lengths, risk_counts, strict=True)
    }


def stratified_sample(contracts: list[Contract], limit: int, seed: int, categories: RiskCategories) -> list[Contract]:
    """
    `limit` contracts spread across the nine strata, deterministically.

    Within a stratum the order is shuffled by the seeded generator and the
    strata are visited in a fixed order, so the draw does not depend on how CUAD
    happened to list the contracts. Returned in corpus order, which keeps a
    results file readable.
    """
    if limit >= len(contracts):
        return list(contracts)

    strata = assign_strata(contracts, categories)
    rng = random.Random(seed)

    grouped: dict[Stratum, list[Contract]] = {}
    for contract in contracts:
        grouped.setdefault(strata[contract.title], []).append(contract)

    order = sorted(grouped, key=lambda stratum: (stratum.length, stratum.risk_count))

    for stratum in order:
        # sorted before shuffling, and the strata visited in key order rather than
        # in insertion order, so neither the contracts' order in CUAD nor the order
        # the buckets were first seen in can reach the generator
        bucket = grouped[stratum]
        bucket.sort(key=lambda contract: contract.title)
        rng.shuffle(bucket)

    chosen: list[Contract] = []

    while len(chosen) < limit:
        drawn = False
        for stratum in order:
            bucket = grouped[stratum]
            if not bucket:
                continue
            chosen.append(bucket.pop())
            drawn = True
            if len(chosen) == limit:
                break
        if not drawn:
            break

    titles = {contract.title for contract in chosen}

    return [contract for contract in contracts if contract.title in titles]
