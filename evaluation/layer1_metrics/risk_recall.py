"""Layer 1b: the product metric — did the review surface the risky clauses?

The review runs as it runs in production: parse, batch, prompts/legal_advice.py,
merge, verify_risks. Then, of the CUAD spans in the risky categories, count how
many some risk's quote covers at Jaccard >= 0.5.

Three things this file is careful about, because each is a way to flatter the
system:

*Precision.* CUAD annotates 41 categories. A risk we flag outside them — an
indemnity, a blank governing law, a 90-day payment term — is not a false
positive, it is a finding the benchmark cannot see. Those are counted and listed
as `off_benchmark_findings`. `precision_lower_bound` is reported with every one
of them still in the denominator, and is labelled a lower bound because that is
what it is.

*Ranking.* The review has no confidence knob, so there is no threshold to sweep.
Severity is used as the ranking instead: critical, then critical+high, and so on,
four operating points. The area under the resulting curve is called
`aupr_by_severity` and is not comparable to the paper's AUPR, which comes from a
continuous score. `per_risk` keeps every risk with its severity and its match, so
the day the prompt returns a confidence this can be rescored without paying for
the reviews again.

*Quotes.* A risk whose quote the evidence check could not find is still counted
as covering a span if its text matches, because recall is about whether the
clause was surfaced. `unverified_coverage` says how many of the covered spans
were covered by an unverified quote, which is the number to watch: coverage that
rests on a quote the document does not contain is coverage a reviewer cannot
trust.
"""

from dataclasses import dataclass, field

from enums.RiskSeverity import RiskSeverity
from evaluation.common.pipeline import ReviewResult
from evaluation.common.spans import JACCARD_THRESHOLD, covers_any, jaccard
from evaluation.cuad.loader import Contract, RiskCategories
from schemas.key_risk import KeyRisk

# worst first: the order the operating points are added in
SEVERITY_ORDER = (RiskSeverity.CRITICAL, RiskSeverity.HIGH, RiskSeverity.MEDIUM, RiskSeverity.LOW)


@dataclass(frozen=True)
class GoldSpan:
    """One annotated span in a risky category, and whether anything covered it."""

    document: str
    category: str
    text: str


@dataclass
class RiskRecord:
    """
    One risk the review reported, and what it turned out to cover.

    Kept in the results file so the whole metric can be recomputed for free, and
    so a model-reported confidence could be scored later without re-reviewing.
    """

    document: str
    description: str
    severity: str
    location: str
    quote: str
    page: int | None
    quote_verified: bool
    covered_category: str = ""
    covered_span: str = ""
    match_score: float = 0.0

    @property
    def on_benchmark(self) -> bool:
        return bool(self.covered_category)

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "description": self.description,
            "severity": self.severity,
            "location": self.location,
            "quote": self.quote,
            "page": self.page,
            "quote_verified": self.quote_verified,
            "covered_category": self.covered_category,
            "covered_span": self.covered_span,
            "match_score": round(self.match_score, 4),
        }

    @classmethod
    def from_dict(cls, record: dict) -> "RiskRecord":
        return cls(**{key: record[key] for key in record if key in cls.__dataclass_fields__})


def gold_spans(contract: Contract, categories: RiskCategories) -> list[GoldSpan]:
    """Every annotated span of every risky category in one contract."""
    return [
        GoldSpan(document=contract.title, category=clause.category, text=span)
        for clause in contract.annotated_clauses(set(categories.risky))
        for span in clause.spans
    ]


def _best_cover(risk: KeyRisk, spans: list[GoldSpan], threshold: float) -> tuple[int | None, float]:
    """
    The span this risk's quote covers best, by index, and the score.

    Matching is on the quote alone, not the description. A description says
    "liability is unlimited", which shares words with any liability clause in the
    contract; the quote is the claim about where the risk is, and it is the claim
    worth scoring.
    """
    if not risk.quote:
        return None, 0.0

    index = covers_any(risk.quote, [span.text for span in spans], threshold)
    if index is None:
        return None, 0.0

    return index, jaccard(risk.quote, spans[index].text)


def build_records(review: ReviewResult, spans: list[GoldSpan], threshold: float = JACCARD_THRESHOLD) -> list[RiskRecord]:
    """One RiskRecord per reported risk, annotated with what it covered."""
    records = []

    for risk in review.advice.key_risks:
        index, score = _best_cover(risk, spans, threshold)
        records.append(
            RiskRecord(
                document=review.document,
                description=risk.description,
                severity=risk.severity.value,
                location=risk.location,
                quote=risk.quote,
                page=risk.page,
                quote_verified=risk.quote_verified,
                covered_category=spans[index].category if index is not None else "",
                covered_span=spans[index].text if index is not None else "",
                match_score=score,
            )
        )

    return records


def _covered_keys(records: list[RiskRecord]) -> set[tuple[str, str, str]]:
    """The identity of each covered span: which document, category and text."""
    return {(record.document, record.covered_category, record.covered_span) for record in records if record.on_benchmark}


def severity_curve(records: list[RiskRecord], gold_total: int) -> list[dict]:
    """
    Four operating points, from taking only critical risks to taking all of them.

    Precision at each point counts every risk in the point, off-benchmark ones
    included, so it is a lower bound. Recall is over the risky gold spans.
    """
    points = []
    included: list[RiskRecord] = []
    seen: set[str] = set()

    for severity in SEVERITY_ORDER:
        seen.add(severity.value)
        included = [record for record in records if record.severity in seen]

        covered = len(_covered_keys(included))
        points.append(
            {
                "severity_at_least": severity.value,
                "risks": len(included),
                "covered_spans": covered,
                "recall": round(covered / gold_total, 4) if gold_total else 0.0,
                "precision_lower_bound": (
                    round(sum(1 for record in included if record.on_benchmark) / len(included), 4) if included else 0.0
                ),
            }
        )

    return points


def area_under_curve(points: list[dict]) -> float:
    """
    Trapezoidal area under the four-point precision/recall curve.

    Anchored at recall 0 with the highest-severity precision, because the curve
    otherwise starts wherever the critical risks happened to land and the area
    would depend on how many of them there were. Four points is not enough for
    this to mean what AUPR means; it is here so two runs can be compared.
    """
    if not points:
        return 0.0

    curve = [(0.0, points[0]["precision_lower_bound"])]
    curve += [(point["recall"], point["precision_lower_bound"]) for point in points]
    curve.sort()

    area = 0.0
    for (left_recall, left_precision), (right_recall, right_precision) in zip(curve, curve[1:], strict=False):
        area += (right_recall - left_recall) * (left_precision + right_precision) / 2

    return round(area, 4)


@dataclass
class RiskRecallScore:
    """The whole of layer 1b for a set of contracts."""

    gold_spans: int = 0
    covered_spans: int = 0
    risks_reported: int = 0
    unverified_coverage: int = 0
    per_category: dict[str, dict] = field(default_factory=dict)
    off_benchmark_findings: list[dict] = field(default_factory=list)
    curve: list[dict] = field(default_factory=list)
    aupr_by_severity: float = 0.0
    failed_documents: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.covered_spans / self.gold_spans if self.gold_spans else 0.0

    @property
    def miss_rate(self) -> float:
        """The headline: risky spans nothing in the review surfaced."""
        return 1.0 - self.recall

    def to_dict(self) -> dict:
        return {
            "note": (
                "Recall of CUAD's risky annotated spans by the real review pipeline. "
                "aupr_by_severity is four severity operating points, NOT the CUAD paper's AUPR. "
                "Risks outside CUAD's 41 categories are off_benchmark_findings, not false positives."
            ),
            "gold_spans": self.gold_spans,
            "covered_spans": self.covered_spans,
            "recall": round(self.recall, 4),
            "miss_rate": round(self.miss_rate, 4),
            "risks_reported": self.risks_reported,
            "unverified_coverage": self.unverified_coverage,
            "aupr_by_severity": self.aupr_by_severity,
            "curve": self.curve,
            "off_benchmark_findings": {
                "count": len(self.off_benchmark_findings),
                "findings": self.off_benchmark_findings,
            },
            "per_category": self.per_category,
            "failed_documents": self.failed_documents,
        }


def score_risk_recall(
    pairs: list[tuple[Contract, ReviewResult]],
    categories: RiskCategories,
    threshold: float = JACCARD_THRESHOLD,
) -> tuple[RiskRecallScore, list[RiskRecord]]:
    """
    Scores every reviewed contract and returns the per-risk records with it.

    A document whose review failed is named in `failed_documents` and its gold
    spans still count against recall: a crash is a miss, and excluding it would
    make an unreliable pipeline look accurate.
    """
    score = RiskRecallScore()
    all_records: list[RiskRecord] = []
    per_category: dict[str, dict] = {}

    for contract, review in pairs:
        spans = gold_spans(contract, categories)
        score.gold_spans += len(spans)

        for span in spans:
            entry = per_category.setdefault(span.category, {"gold_spans": 0, "covered_spans": 0})
            entry["gold_spans"] += 1

        if review.error:
            score.failed_documents.append(f"{review.document}: {review.error}")
            continue

        records = build_records(review, spans, threshold)
        all_records.extend(records)
        score.risks_reported += len(records)

        covered = _covered_keys(records)
        score.covered_spans += len(covered)

        for _, category, _ in covered:
            per_category[category]["covered_spans"] += 1

        score.unverified_coverage += len(
            {
                (record.document, record.covered_category, record.covered_span)
                for record in records
                if record.on_benchmark and not record.quote_verified
            }
        )

        score.off_benchmark_findings.extend(
            {
                "document": record.document,
                "description": record.description,
                "severity": record.severity,
                "location": record.location,
                "quote_verified": record.quote_verified,
            }
            for record in records
            if not record.on_benchmark
        )

    for category, entry in per_category.items():
        entry["recall"] = round(entry["covered_spans"] / entry["gold_spans"], 4) if entry["gold_spans"] else 0.0
        per_category[category] = entry

    score.per_category = dict(sorted(per_category.items()))
    score.curve = severity_curve(all_records, score.gold_spans)
    score.aupr_by_severity = area_under_curve(score.curve)

    return score, all_records
