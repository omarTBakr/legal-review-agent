"""Deciding which judge verdicts a human has to look at.

Three reasons, any one of them enough:

  * an unclear score — 0.3 < total_score < 0.8. Below 0.3 the finding is plainly
    wrong and below 0.8 it is plainly imperfect; between them the judge has said
    "some of this is right", which is the case a rubric cannot settle;
  * a hedged reason — the rubric asks the judge to write "uncertain" or
    "borderline" when it is unsure, and a judge that does should be believed;
  * a high-stakes category — liability, IP and termination, per
    evaluation/cuad/risk_categories.json. A confident judge is still only a
    model, and these are the clauses where being wrong costs a client money.

An ungraded finding — the judge call failed — escalates too. It has no score to
be confident about.

The queue is JSONL and append-only, so a run that dies half way leaves the
verdicts it managed to escalate, and two runs' queues concatenate.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from evaluation.layer2_judge.judge import JudgeVerdict

# the judge's own hedges, as the rubric asks for them, plus the ones models
# reach for instead
HEDGE_WORDS = (
    "uncertain",
    "borderline",
    "unclear",
    "ambiguous",
    "arguably",
    "hard to say",
    "difficult to say",
    "could be argued",
    "not confident",
    "possibly",
)


@dataclass(frozen=True)
class Escalation:
    """One verdict bound for a human, with why it was sent."""

    verdict: JudgeVerdict
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return {**self.verdict.to_dict(), "escalation_reasons": list(self.reasons)}


def hedges(reason: str) -> bool:
    """Whether the judge's one-sentence reason admits to doubt."""
    lowered = reason.lower()

    return any(word in lowered for word in HEDGE_WORDS)


def escalation_reasons(
    verdict: JudgeVerdict,
    high_stakes: frozenset[str] | set[str],
    below: float = 0.8,
    above: float = 0.3,
) -> tuple[str, ...]:
    """
    Every reason this verdict needs a human, or an empty tuple.

    All the reasons rather than the first: a borderline score on a liability
    clause is a different queue item from a borderline score on an audit right,
    and whoever triages the queue should be able to see that without re-deriving
    it.
    """
    reasons = []

    if verdict.error:
        reasons.append("the judge failed to grade it")
    else:
        if above < verdict.total_score < below:
            reasons.append(f"unclear score ({verdict.total_score:.2f})")
        if hedges(verdict.reason):
            reasons.append("the judge hedged")

    if verdict.category in high_stakes:
        reasons.append(f"high-stakes category ({verdict.category})")

    return tuple(reasons)


def should_escalate(
    verdict: JudgeVerdict, high_stakes: frozenset[str] | set[str], below: float = 0.8, above: float = 0.3
) -> bool:
    """Whether this verdict goes to a human at all."""
    return bool(escalation_reasons(verdict, high_stakes, below, above))


def build_queue(
    verdicts: list[JudgeVerdict],
    high_stakes: frozenset[str] | set[str],
    below: float = 0.8,
    above: float = 0.3,
) -> list[Escalation]:
    """
    The queue, worst score first.

    Ordered so that a reviewer with time for ten of them spends it on the ten
    most likely to be wrong, rather than on whichever contract sorted first.
    """
    queue = [
        Escalation(verdict=verdict, reasons=reasons)
        for verdict in verdicts
        if (reasons := escalation_reasons(verdict, high_stakes, below, above))
    ]
    queue.sort(key=lambda item: (item.verdict.total_score, item.verdict.document, item.verdict.category))

    return queue


def write_queue(queue: list[Escalation], path: Path) -> Path:
    """Appends the queue to a JSONL file and returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        for item in queue:
            handle.write(json.dumps(item.to_dict()) + "\n")

    return path


def read_queue(path: Path) -> list[dict]:
    """The queue as written, skipping blank lines."""
    if not path.is_file():
        return []

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
