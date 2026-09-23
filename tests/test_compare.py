"""
Comparing two rounds of the same contract.

No model anywhere in this: the comparison is quote matching, which is why it can
be tested exactly rather than approximately. The cases worth pinning down are
the ones where a reviewer would be misled — a reworded clause reading as
"unchanged", a renamed upload reading as "everything was fixed".
"""

import pytest

from enums.RiskSeverity import RiskSeverity
from schemas.key_risk import KeyRisk
from schemas.legal_advice import LegalAdvice
from utils.compare import (
    BETTER,
    FIXED,
    NEW,
    UNCHANGED,
    WORSE,
    compare_advice,
    compare_risks,
    pair_documents,
)

CAP = "The Supplier's total liability under this Agreement shall be unlimited in all respects."
CAPPED = "The Supplier's total liability under this Agreement shall not exceed the fees paid in the preceding 12 months."
INDEMNITY = "The Client shall indemnify the Supplier against any and all claims arising from the Services."
TERMINATION = "The Supplier may terminate this Agreement at any time for convenience on 5 days notice."


def risk(description: str, severity: str, quote: str, page: int = 1) -> KeyRisk:
    return KeyRisk(
        description=description,
        severity=RiskSeverity.parse(severity),
        location="",
        quote=quote,
        page=page,
        quote_verified=True,
    )


def advice(*risks: KeyRisk) -> LegalAdvice:
    return LegalAdvice(summary="", key_risks=list(risks), needs_human=False, question="")


def verdicts(changes) -> dict[str, list[str]]:
    """{verdict: [description, ...]}, which is what the assertions care about."""
    grouped: dict[str, list[str]] = {}
    for change in changes:
        grouped.setdefault(change.verdict, []).append(change.description)

    return grouped


def test_a_risk_that_is_gone_reads_as_fixed():
    changes = compare_risks([risk("liability is unlimited", "critical", CAP)], [])

    assert [change.verdict for change in changes] == [FIXED]
    assert changes[0].severity == "critical"


def test_a_risk_that_was_not_there_reads_as_new():
    changes = compare_risks([], [risk("one-sided indemnity", "high", INDEMNITY)])

    assert [change.verdict for change in changes] == [NEW]


def test_the_same_clause_still_there_reads_as_unchanged():
    before = [risk("liability is unlimited", "critical", CAP)]
    after = [risk("liability is still unlimited", "critical", CAP)]

    changes = compare_risks(before, after)

    assert [change.verdict for change in changes] == [UNCHANGED]
    assert changes[0].was_description == "liability is unlimited"
    assert changes[0].match_score == 1.0


def test_a_severity_that_rose_reads_as_worse():
    changes = compare_risks(
        [risk("termination for convenience", "medium", TERMINATION)],
        [risk("termination for convenience", "high", TERMINATION)],
    )

    assert [change.verdict for change in changes] == [WORSE]
    assert (changes[0].was_severity, changes[0].severity) == ("medium", "high")


def test_a_severity_that_fell_reads_as_better():
    changes = compare_risks(
        [risk("termination for convenience", "high", TERMINATION)],
        [risk("termination for convenience", "low", TERMINATION)],
    )

    assert [change.verdict for change in changes] == [BETTER]


def test_a_reworded_clause_is_one_fixed_and_one_new():
    """
    The honest answer. Below the matching threshold it is not the same sentence
    any more, and reporting "unchanged" would tell a reviewer the cap was never
    added.
    """
    changes = compare_risks(
        [risk("liability is unlimited", "critical", CAP)],
        [risk("cap is low but it exists", "medium", CAPPED)],
    )

    grouped = verdicts(changes)

    assert grouped[FIXED] == ["liability is unlimited"]
    assert grouped[NEW] == ["cap is low but it exists"]


def test_a_round_that_fixed_one_and_added_another():
    before = [risk("liability is unlimited", "critical", CAP)]
    after = [risk("one-sided indemnity", "high", INDEMNITY)]

    comparison = compare_advice("round-2.pdf", advice(*before), advice(*after), "round-1.pdf")

    assert comparison.resolved == 1
    assert comparison.introduced == 1
    assert comparison.base_document == "round-1.pdf"


def test_worst_news_sorts_where_it_will_be_read():
    """Fixed first, then better, unchanged, worse, new — and severity within."""
    before = [
        risk("liability is unlimited", "critical", CAP),
        risk("termination for convenience", "medium", TERMINATION),
    ]
    after = [
        risk("termination for convenience", "high", TERMINATION),
        risk("one-sided indemnity", "critical", INDEMNITY),
    ]

    changes = compare_risks(before, after)

    assert [change.verdict for change in changes] == [FIXED, WORSE, NEW]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        # a fixed critical is worth -4, nothing else moved
        ([("critical", CAP)], [], -4),
        # a new medium is +2
        ([], [("medium", TERMINATION)], 2),
        # traded a critical away for a medium: a clear improvement
        ([("critical", CAP)], [("medium", TERMINATION)], -2),
        # nothing moved
        ([("high", INDEMNITY)], [("high", INDEMNITY)], 0),
        # same clause, one band worse
        ([("medium", TERMINATION)], [("high", TERMINATION)], 1),
    ],
)
def test_net_severity_change(before, after, expected):
    comparison = compare_advice(
        "d.pdf",
        advice(*(risk("x", severity, quote) for severity, quote in before)),
        advice(*(risk("x", severity, quote) for severity, quote in after)),
    )

    assert comparison.net_severity_change == expected


def test_counts_cover_every_verdict():
    """A missing key in the UI's totals row would render as undefined."""
    comparison = compare_advice("d.pdf", advice(risk("x", "high", CAP)), advice())

    assert set(comparison.counts) == {FIXED, BETTER, UNCHANGED, WORSE, NEW}
    assert comparison.counts[FIXED] == 1


def test_an_empty_quote_never_matches():
    """
    Advice stored before quotes existed has none. Two such risks must not pair
    with each other just for both being empty.
    """
    before = [KeyRisk(description="old risk", severity=RiskSeverity.HIGH)]
    after = [KeyRisk(description="different risk", severity=RiskSeverity.HIGH)]

    assert verdicts(compare_risks(before, after)) == {FIXED: ["old risk"], NEW: ["different risk"]}


class TestPairDocuments:
    def test_one_each_side_pairs_whatever_they_are_called(self):
        """Re-uploading round two under a tidier name is not "all fixed, all new"."""
        pairs = pair_documents(["p/draft-a1b2c3d4.pdf"], ["p/final-version-99887766.pdf"])

        assert pairs == [("p/draft-a1b2c3d4.pdf", "p/final-version-99887766.pdf")]

    def test_several_documents_pair_on_the_name(self):
        pairs = pair_documents(
            ["p/services-aaaaaaaa.pdf", "p/nda-bbbbbbbb.pdf"],
            ["p/nda-cccccccc.pdf", "p/services-dddddddd.pdf"],
        )

        assert sorted(pairs) == [
            ("p/nda-bbbbbbbb.pdf", "p/nda-cccccccc.pdf"),
            ("p/services-aaaaaaaa.pdf", "p/services-dddddddd.pdf"),
        ]

    def test_a_document_with_no_counterpart_is_left_unpaired(self):
        pairs = pair_documents(
            ["p/services-aaaaaaaa.pdf", "p/nda-bbbbbbbb.pdf"],
            ["p/services-dddddddd.pdf", "p/schedule-eeeeeeee.pdf"],
        )

        assert pairs == [("p/services-aaaaaaaa.pdf", "p/services-dddddddd.pdf")]

    def test_a_name_that_is_not_suffixed_still_pairs(self):
        """Keys from before the suffix, or written by hand."""
        pairs = pair_documents(["p/a.pdf", "p/b.pdf"], ["p/b.pdf", "p/a.pdf"])

        assert sorted(pairs) == [("p/a.pdf", "p/a.pdf"), ("p/b.pdf", "p/b.pdf")]

    def test_a_hyphenated_name_is_not_mistaken_for_a_suffix(self):
        """Only eight hex characters count; "agreement" must survive intact."""
        pairs = pair_documents(
            ["p/master-services-agreement.pdf", "p/x-11111111.pdf"],
            ["p/master-services-agreement.pdf", "p/y-22222222.pdf"],
        )

        assert pairs == [("p/master-services-agreement.pdf", "p/master-services-agreement.pdf")]
