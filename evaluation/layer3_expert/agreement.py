"""Do the models agree with the humans?

The judge exists to save human time, and the only evidence that it does is that
the humans reach the judge's conclusion when they look. This reduces both sides
to one bit — did this finding pass? — and compares them.

On Cohen's kappa: it measures agreement between *two annotators*, correcting for
the agreement two coin flips with the same bias would reach by chance. With one
human it has no second annotator's marginal distribution to correct against, and
computing it against the judge's marginals would be measuring the judge against
itself. `cohens_kappa` therefore returns None with a reason until at least two
humans have reviewed overlapping items, and `judge_vs_human` reports raw
agreement, which is honest and unglamorous, in the meantime.
"""

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# the human answers that count as "the system got this right"
PASSING_ANSWERS = {"yes"}

MIN_ANNOTATORS = 2


def human_pass(record: dict) -> bool | None:
    """
    The human's verdict as one bit, or None when they did not settle it.

    "partially" is not folded into either: a finding that is partly right is the
    thing the judge's 0-or-1 rubric cannot express, and averaging it away would
    hide exactly the cases layer 3 exists to catch.
    """
    answer = record.get("risk_identified")
    if answer in PASSING_ANSWERS:
        return True
    if answer == "no":
        return False

    return None


def _key(record: dict) -> tuple[str, str, str]:
    return record.get("document", ""), record.get("category", ""), record.get("quote", "")


@dataclass(frozen=True)
class Agreement:
    """Raw agreement between the judge and the humans over the items both saw."""

    compared: int
    agreed: int
    judge_pass_human_fail: int
    judge_fail_human_pass: int
    undecided_by_human: int
    annotators: tuple[str, ...]

    @property
    def rate(self) -> float:
        return self.agreed / self.compared if self.compared else 0.0

    def to_dict(self) -> dict:
        kappa, note = cohens_kappa_note(self.annotators)

        return {
            "compared": self.compared,
            "agreed": self.agreed,
            "agreement_rate": round(self.rate, 4),
            "judge_passed_human_failed": self.judge_pass_human_fail,
            "judge_failed_human_passed": self.judge_fail_human_pass,
            "undecided_by_human": self.undecided_by_human,
            "annotators": list(self.annotators),
            "cohens_kappa": kappa,
            "cohens_kappa_note": note,
        }


def judge_vs_human(reviews: list[dict]) -> Agreement:
    """
    Compares each human review against the judge verdict recorded with it.

    The judge's verdict travels in the human's own record — review.py copies
    judge_pass in when it writes the form — so this needs no second file and
    cannot pair a human answer with a verdict from a different run.
    """
    compared = agreed = judge_pass_human_fail = judge_fail_human_pass = undecided = 0
    annotators = Counter()

    for record in reviews:
        annotators[str(record.get("reviewer", "")) or "anonymous"] += 1

        human = human_pass(record)
        if human is None:
            undecided += 1
            continue

        judge = bool(record.get("judge_pass"))
        compared += 1

        if judge == human:
            agreed += 1
        elif judge:
            judge_pass_human_fail += 1
        else:
            judge_fail_human_pass += 1

    return Agreement(
        compared=compared,
        agreed=agreed,
        judge_pass_human_fail=judge_pass_human_fail,
        judge_fail_human_pass=judge_fail_human_pass,
        undecided_by_human=undecided,
        annotators=tuple(sorted(annotators)),
    )


def cohens_kappa(reviews: list[dict]) -> float | None:
    """
    Cohen's kappa between two human annotators on the items both reviewed.

    Returns None when there are fewer than two annotators, or when they share no
    items, or when neither ever disagreed with themselves across categories so
    that the expected agreement is 1 and kappa is undefined. Call
    cohens_kappa_note for the reason.
    """
    by_reviewer: dict[str, dict[tuple[str, str, str], bool | None]] = defaultdict(dict)

    for record in reviews:
        reviewer = str(record.get("reviewer", "")) or "anonymous"
        by_reviewer[reviewer][_key(record)] = human_pass(record)

    if len(by_reviewer) < MIN_ANNOTATORS:
        return None

    first, second = (by_reviewer[name] for name in sorted(by_reviewer)[:MIN_ANNOTATORS])
    shared = [key for key in first if key in second and first[key] is not None and second[key] is not None]

    if not shared:
        return None

    left = [first[key] for key in shared]
    right = [second[key] for key in shared]

    observed = sum(1 for a, b in zip(left, right, strict=True) if a == b) / len(shared)

    # chance agreement from each annotator's own rate of saying "pass"
    left_pass = sum(left) / len(left)
    right_pass = sum(right) / len(right)
    expected = left_pass * right_pass + (1 - left_pass) * (1 - right_pass)

    if expected == 1:
        return None

    return round((observed - expected) / (1 - expected), 4)


def cohens_kappa_note(annotators: tuple[str, ...]) -> tuple[float | None, str]:
    """The kappa value and, when it is None, why."""
    if len(annotators) < MIN_ANNOTATORS:
        return None, (
            f"not computed: Cohen's kappa needs two annotators and there {'is' if annotators else 'are'} "
            f"{len(annotators)}. Raw agreement with the judge is reported instead; it is not chance-corrected."
        )

    return None, "call cohens_kappa(reviews) with the review records to compute it"


def load_reviews(path: Path) -> list[dict]:
    """The human reviews from a run directory or a JSONL file."""
    if path.is_dir():
        path = path / "human_reviews.jsonl"

    if not path.is_file():
        return []

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# what an expert decision means as the same one bit the human and judge give
EXPERT_PASS = {"upheld": True, "overturned": False}


def expert_vs_human(reviews: list[dict]) -> dict:
    """
    How often the expert model reached the lawyer's conclusion.

    This is the number that says whether the expensive model is worth its place.
    The judge's agreement is measured over everything it graded; this is measured
    only over the items the expert could *not* settle, because those are the ones
    a human sees — so it is the harder test of the two and will read lower. A
    "partial" is left out on both sides for the reason human_pass leaves out
    "partially": it is the answer a one-bit comparison cannot hold.
    """
    compared = agreed = expert_pass_human_fail = expert_fail_human_pass = 0
    undecided = 0

    for record in reviews:
        expert = EXPERT_PASS.get(str(record.get("expert_decision") or ""))
        human = human_pass(record)

        if expert is None or human is None:
            undecided += 1
            continue

        compared += 1
        if expert == human:
            agreed += 1
        elif expert:
            expert_pass_human_fail += 1
        else:
            expert_fail_human_pass += 1

    return {
        "compared": compared,
        "agreed": agreed,
        "agreement_rate": round(agreed / compared, 4) if compared else 0.0,
        "expert_upheld_human_failed": expert_pass_human_fail,
        "expert_overturned_human_passed": expert_fail_human_pass,
        "not_comparable": undecided,
    }


def report(reviews: list[dict]) -> dict:
    """Everything layer 3 can say, with the kappa caveat attached."""
    agreement = judge_vs_human(reviews)
    payload = agreement.to_dict()
    payload["expert_vs_human"] = expert_vs_human(reviews)

    kappa = cohens_kappa(reviews)
    if kappa is not None:
        payload["cohens_kappa"] = kappa
        payload["cohens_kappa_note"] = f"between {agreement.annotators[0]} and {agreement.annotators[1]} on shared items"

    payload["human_reviews"] = len(reviews)

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge-versus-human agreement for a run.")
    parser.add_argument("run", type=Path, help="a results/<timestamp> directory or a human_reviews.jsonl")
    arguments = parser.parse_args()

    reviews = load_reviews(arguments.run)
    if not reviews:
        print(f"No human reviews found under {arguments.run}")
        return

    print(json.dumps(report(reviews), indent=2))


if __name__ == "__main__":
    main()
