"""Reviews the two testingDocs contracts and scores them against expected.json.

    uv run python -m evaluation.fixtures.run
    uv run python -m evaluation.fixtures.run --json          # the scorecard, for CI
    uv run python -m evaluation.fixtures.run --replay DIR     # re-score, no model calls

Both documents at once, because they are small and a serial run is two round
trips of waiting for nothing.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from evaluation.common.meter import TokenMeter
from evaluation.common.pipeline import result_from_dict, review_pdf
from evaluation.config import RESULTS_DIR
from evaluation.fixtures.scorer import load_expected, score_document, summarize
from utils.config import get_setting


async def review_all(documents, meter: TokenMeter, pages_per_batch: int):
    """Reviews every fixture document through the real parser and pipeline."""
    llm = meter.llm(get_setting())

    try:
        return await asyncio.gather(
            *(review_pdf(llm, document.filename, document.path, pages_per_batch) for document in documents)
        )
    finally:
        await llm.aclose()


def print_report(report: dict) -> None:
    """The scorecard as a few lines, because this runs on every prompt change."""
    summary = report["summary"]

    print("")
    print(f"  recall                    {summary['found']}/{summary['expectations']}  ({summary['recall']:.0%})")
    print(f"  quote was the right one   {summary['anchor_hits']}/{summary['scoreable']}  ({summary['anchor_hit_rate']:.0%})")
    print(f"  severity within one band  {summary['severity_agreement_within_one_band']:.0%}")
    print(f"  quotes verified           {summary['quote_verification_rate']:.0%} of {summary['risks_reported']} risk(s)")
    print(f"  needs_human as expected   {summary['needs_human_as_expected']}/{summary['documents']}")
    print(f"  extra findings            {summary['extra_findings']}")

    if summary["missed"]:
        print("\n  MISSED:")
        for identifier in summary["missed"]:
            print(f"    - {identifier}")

    if summary["failed_documents"]:
        print(f"\n  FAILED: {', '.join(summary['failed_documents'])}")

    print(f"\n  {report['usage']['summary']}")
    print(f"  written to {report['results_dir']}")


async def main_async(arguments) -> dict:
    settings = get_setting()
    documents = load_expected()
    meter = TokenMeter()

    if arguments.replay:
        recorded = {
            record["document"]: result_from_dict(record)
            for record in (
                json.loads(line)
                for line in (arguments.replay / "reviews.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }
        reviews = [recorded[document.filename] for document in documents if document.filename in recorded]
        documents = [document for document in documents if document.filename in recorded]
        results_dir = arguments.replay
    else:
        await meter.load_prices()
        reviews = await review_all(documents, meter, settings.legal_pages_per_batch)
        results_dir = RESULTS_DIR / f"fixtures-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "reviews.jsonl").write_text(
            "".join(json.dumps(review.to_dict()) + "\n" for review in reviews), encoding="utf-8"
        )

    scores = [score_document(document, review) for document, review in zip(documents, reviews, strict=True)]

    report = {
        "suite": "fixtures",
        "reviewer_model": settings.openrouter_model,
        "summary": summarize(scores),
        "documents": [score.to_dict() for score in scores],
        "usage": {**meter.report(), "summary": meter.summary_line()},
        "results_dir": str(results_dir),
    }

    (results_dir / "fixtures.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a review of testingDocs against expected.json.")
    parser.add_argument("--json", action="store_true", help="print the whole scorecard as JSON")
    parser.add_argument("--replay", type=Path, help="score a recorded run in this results directory, no model calls")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help="exit non-zero when recall falls below this, so the run can gate a prompt change in CI",
    )
    arguments = parser.parse_args()

    report = asyncio.run(main_async(arguments))

    if arguments.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)

    if report["summary"]["recall"] < arguments.fail_under:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
