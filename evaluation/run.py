"""The CUAD run: sample, review, score, judge, escalate, adjudicate, scorecard.

Three models, one per level, each stronger than the one below and none of them
the same: the reviewer under test (OPENROUTER_MODEL), the judge that grades every
matched finding (EVAL_JUDGE_MODEL), and the expert that re-decides only what the
judge escalated (EVAL_ADJUDICATOR_MODEL). The cost falls as the models get
dearer, because each level sees a fraction of what the one below it did.


    uv run python -m evaluation.run --limit 25          # the default: 25 contracts, fixed seed
    uv run python -m evaluation.run --all               # all 510; read the cost estimate first
    uv run python -m evaluation.run --dry-run           # re-score the last run, no model calls
    uv run python -m evaluation.run --compare old.json  # this run against a previous scorecard

Everything a model said is written to evaluation/results/<timestamp>/ before it
is scored, so changing a metric costs nothing to re-measure: --dry-run reads that
directory instead of calling anything.

The headline number is the end-to-end miss rate — risky CUAD clauses that no
layer caught. It is printed last and it is the one to quote.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from enums.RiskSeverity import RiskSeverity
from evaluation.common.meter import TokenMeter
from evaluation.common.models import reviewer_model, reviewer_setting
from evaluation.common.pipeline import result_from_dict, review_text
from evaluation.config import DATA_DIR, RESULTS_DIR, get_eval_settings
from evaluation.cuad.download import ATTRIBUTION, ensure_dataset
from evaluation.cuad.loader import Contract, load_contracts, load_risk_categories
from evaluation.cuad.sampling import stratified_sample
from evaluation.layer1_metrics.extraction import ExtractionAttempt, extract_contract, score_extraction
from evaluation.layer1_metrics.risk_recall import RiskRecord, score_risk_recall
from evaluation.layer2_judge.escalation import Escalation, build_queue, read_queue, write_queue
from evaluation.layer2_judge.judge import JudgeVerdict, judge_all, summarize
from evaluation.layer3_expert.adjudicator import (
    ADJUDICATIONS_FILE,
    Adjudication,
    adjudicate_all,
    human_queue,
    load_adjudications,
    write_adjudications,
)
from evaluation.layer3_expert.adjudicator import summarize as summarize_adjudications
from evaluation.layer3_expert.agreement import load_reviews
from evaluation.layer3_expert.agreement import report as agreement_report
from schemas.key_risk import KeyRisk
from utils.config import get_setting
from utils.logger import get_logger

logger = get_logger(__name__)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_run(results_dir: Path = RESULTS_DIR) -> Path | None:
    """The most recent CUAD run directory, for --dry-run with no --replay."""
    candidates = sorted(path for path in results_dir.glob("cuad-*") if (path / "reviews.jsonl").is_file())

    return candidates[-1] if candidates else None


async def run_reviews(contracts: list[Contract], meter: TokenMeter, concurrency: int) -> list:
    """Reviews every sampled contract through the real pipeline, capped concurrency."""
    settings = get_setting()
    eval_settings = get_eval_settings()
    llm = meter.llm(settings)
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(contract: Contract):
        async with semaphore:
            logger.info("reviewing %s (%d chars)", contract.title, len(contract.text))
            return await review_text(
                llm,
                contract.title,
                contract.text,
                settings.legal_pages_per_batch,
                eval_settings.page_characters,
            )

    try:
        return list(await asyncio.gather(*(guarded(contract) for contract in contracts)))
    finally:
        await llm.aclose()


async def run_extraction(
    contracts: list[Contract], categories: set[str] | None, meter: TokenMeter, concurrency: int
) -> list[ExtractionAttempt]:
    """Layer 1a: one call per (contract, category)."""
    settings = get_setting()
    eval_settings = get_eval_settings()
    llm = meter.llm(settings, model=eval_settings.extraction_model)
    semaphore = asyncio.Semaphore(concurrency)

    try:
        batches = await asyncio.gather(*(extract_contract(llm, contract, categories, semaphore) for contract in contracts))
    finally:
        await llm.aclose()

    return [attempt for batch in batches for attempt in batch]


def findings_to_judge(records: list[RiskRecord]) -> list[tuple[str, str, str, KeyRisk]]:
    """
    The risks worth a judge call: the ones that matched a CUAD category.

    Off-benchmark findings are not judged, because the rubric grades a finding
    *against a clause* and there is no annotated clause to grade them against.
    That is a real gap — the review's indemnity findings are never graded by
    anyone but a human — and evaluation/README.md says so.
    """
    return [
        (
            record.document,
            record.covered_category,
            record.covered_span,
            KeyRisk(
                description=record.description,
                severity=RiskSeverity.parse(record.severity),
                location=record.location,
                quote=record.quote,
                page=record.page,
                quote_verified=record.quote_verified,
            ),
        )
        for record in records
        if record.on_benchmark
    ]


def end_to_end_miss_rate(
    risk_score,
    verdicts: list[JudgeVerdict],
    queue: list[Escalation],
    adjudications: list[Adjudication] | None = None,
    pending_human: int | None = None,
) -> dict:
    """
    The headline: risky clauses no layer caught.

    A clause is caught when the review surfaced it and whoever looked hardest at
    the finding agreed with it. A finding the judge scored 0 is a clause the
    review pointed at and described wrongly, which is not a catch — telling a
    client about the wrong risk in the right clause leaves them exposed in the
    same way as saying nothing.

    Where layer 3 has spoken, it and not the judge decides: the expert saw the
    same clause with more capacity, and the point of adding it was to let it
    overturn the judge in both directions. Its `partial` is worth half a catch,
    which is the only honest weight for "the right risk, half of it stated" — so
    `caught` is fractional once layer 3 has run, and says so by being a float.

    An escalated clause the expert settled counts as settled. `pending_human`
    counts the clauses whose catch still rests on nobody's judgement, and is
    passed in from the human queue rather than re-derived, so it cannot disagree
    with the queue the run actually writes.
    """
    failed = {
        (verdict.document, verdict.category, verdict.ground_truth)
        for verdict in verdicts
        if not verdict.error and verdict.total_score == 0.0
    }

    # the expert's best word on each span; several findings can match one clause
    # and the clause is caught if any of them caught it
    expert: dict[tuple[str, str, str], float] = {}
    for item in adjudications or []:
        if item.error or not item.decision:
            continue
        key = (item.document, item.category, item.ground_truth)
        expert[key] = max(expert.get(key, 0.0), item.credit)

    overturned = sum(1 for key, credit in expert.items() if credit == 0.0 and key not in failed)
    rescued = sum(1 for key, credit in expert.items() if credit == 1.0 and key in failed)
    partial = sum(1 for credit in expert.values() if credit == 0.5)

    caught = risk_score.covered_spans - len(failed) + rescued - overturned - partial * 0.5
    total = risk_score.gold_spans
    escalated = {(item.verdict.document, item.verdict.category, item.verdict.ground_truth) for item in queue}

    return {
        "risky_gold_spans": total,
        "surfaced_by_review": risk_score.covered_spans,
        "failed_by_judge": len(failed),
        "overturned_by_expert": overturned,
        "rescued_by_expert": rescued,
        "partial_by_expert": partial,
        "caught": round(caught, 2),
        "missed": round(total - caught, 2),
        "miss_rate": round((total - caught) / total, 4) if total else 0.0,
        "review_only_miss_rate": round(risk_score.miss_rate, 4),
        "pending_human": len(escalated - failed) if pending_human is None else pending_human,
    }


def check_distinct_models(settings, eval_settings, arguments) -> None:
    """
    Refuses a run where two levels share a model.

    Not pedantry: a model grading its own output agrees with itself, and an
    adjudicator that is the judge cannot overturn it. Either would produce a
    number that looks like evidence and is not, which is worse than no number.
    """
    levels = [(reviewer_setting(settings), reviewer_model(settings))]
    if not arguments.no_judge:
        levels.append(("EVAL_JUDGE_MODEL", eval_settings.judge_model))
    if not (arguments.no_judge or arguments.no_adjudicator):
        levels.append(("EVAL_ADJUDICATOR_MODEL", eval_settings.adjudicator_model))

    for index, (name, model) in enumerate(levels):
        for other_name, other_model in levels[index + 1 :]:
            if model == other_model:
                raise SystemExit(
                    f"{name} and {other_name} are both {model}. Each level has to be a different model: "
                    "a model grading or overturning its own output agrees with itself, and the agreement is "
                    "not evidence. Change one, or skip that layer."
                )


def print_scorecard(report: dict) -> None:
    """The run on one screen, in the order the layers run."""
    print("")
    print("=" * 78)
    print(f"  {report['contracts']} CUAD contract(s)   reviewer {report['reviewer_model']}   judge {report['judge_model']}")
    print("=" * 78)

    extraction = report.get("layer1a_extraction")
    if extraction:
        overall = extraction["overall"]
        print("\nLayer 1a — CUAD extraction (single operating point, NOT the paper's AUPR)")
        print(f"  precision {overall['precision']:.3f}   recall {overall['recall']:.3f}   F1 {overall['f1']:.3f}")
        print(f"  correct abstentions {extraction['correct_abstentions']}   spurious answers {extraction['missed_abstentions']}")
    else:
        print("\nLayer 1a — skipped (--no-extraction)")

    risk = report["layer1b_risk_recall"]
    print("\nLayer 1b — risk recall (the product metric)")
    print(f"  recall {risk['recall']:.3f} of {risk['gold_spans']} risky annotated span(s)")
    print(f"  aupr_by_severity {risk['aupr_by_severity']:.3f}   (four severity operating points, not the paper's AUPR)")
    print(f"  off-benchmark findings {risk['off_benchmark_findings']['count']} (counted, not false positives)")
    print(f"  coverage resting on an unverified quote: {risk['unverified_coverage']}")
    if risk["failed_documents"]:
        print(f"  failed reviews: {len(risk['failed_documents'])}")

    judge = report.get("layer2_judge") or {}
    if judge.get("graded"):
        print("\nLayer 2 — judge")
        print(f"  graded {judge['graded']}   pass rate {judge['pass_rate']:.3f}   mean score {judge['mean_total_score']:.3f}")
        print("  by dimension: " + "  ".join(f"{name} {value:.2f}" for name, value in judge["by_dimension"].items()))
        if judge.get("arithmetic_disagreements"):
            print(f"  the judge's own total disagreed with its dimensions {judge['arithmetic_disagreements']} time(s)")
    else:
        print("\nLayer 2 — skipped")

    print(f"\nLayer 3 — {report['escalations']} escalation(s) queued")
    expert = report.get("layer3_adjudication") or {}
    if expert.get("settled") or expert.get("errors"):
        decisions = "  ".join(f"{name} {count}" for name, count in expert["decisions"].items())
        print(f"  expert {report['adjudicator_model']}: settled {expert['settled']}   errors {expert['errors']}")
        print(f"  decisions: {decisions or '(none)'}")
        print(
            f"  disagreed with the judge {expert['disagreed_with_judge']} time(s) — "
            f"overturned {expert['judge_passed_expert_overturned']} pass(es), "
            f"upheld {expert['judge_failed_expert_upheld']} fail(ure)s"
        )
        print(f"  severity wrong on {expert['severity_wrong']}   still needs a lawyer: {expert['still_needs_human']}")
    else:
        print("  no adjudications; the expert model did not run")

    human = report.get("layer3_agreement") or {}
    if human.get("human_reviews"):
        print(f"  {human['human_reviews']} human review(s), agreement {human['agreement_rate']:.3f}")
        print(f"  cohens_kappa: {human['cohens_kappa']} — {human['cohens_kappa_note']}")
    else:
        print(
            f"  {report.get('awaiting_a_lawyer', report['escalations'])} item(s) in human_queue.jsonl; "
            "review them with evaluation.layer3_expert.review"
        )

    headline = report["end_to_end"]
    print("")
    print("-" * 78)
    print(
        f"  END-TO-END MISS RATE   {headline['miss_rate']:.1%}"
        f"   ({headline['missed']:g} of {headline['risky_gold_spans']} risky clauses caught by no layer)"
    )
    print(f"  of which the review never surfaced {headline['risky_gold_spans'] - headline['surfaced_by_review']},")
    print(f"  and the judge failed {headline['failed_by_judge']} it did surface.")
    if expert:
        print(
            f"  the expert then overturned {headline['overturned_by_expert']}, rescued {headline['rescued_by_expert']} "
            f"and half-credited {headline['partial_by_expert']}."
        )
    print(f"  {headline['pending_human']} verdict(s) await a human, so this number is provisional.")
    print("-" * 78)

    print(f"\n  {report['usage']['summary']}")
    print(f"  results: {report['results_dir']}")
    print(f"  {ATTRIBUTION}")


def compare(current: dict, previous: dict) -> None:
    """Prints the numbers that moved between two scorecards."""
    rows = [
        ("end-to-end miss rate", current["end_to_end"]["miss_rate"], previous["end_to_end"]["miss_rate"]),
        ("risk recall", current["layer1b_risk_recall"]["recall"], previous["layer1b_risk_recall"]["recall"]),
        (
            "aupr_by_severity",
            current["layer1b_risk_recall"]["aupr_by_severity"],
            previous["layer1b_risk_recall"]["aupr_by_severity"],
        ),
        (
            "off-benchmark findings",
            current["layer1b_risk_recall"]["off_benchmark_findings"]["count"],
            previous["layer1b_risk_recall"]["off_benchmark_findings"]["count"],
        ),
    ]

    for name, now, before in [
        (
            "extraction F1",
            current.get("layer1a_extraction", {}).get("overall", {}).get("f1"),
            previous.get("layer1a_extraction", {}).get("overall", {}).get("f1"),
        ),
        ("judge pass rate", current.get("layer2_judge", {}).get("pass_rate"), previous.get("layer2_judge", {}).get("pass_rate")),
        (
            "expert disagreed with judge",
            (current.get("layer3_adjudication") or {}).get("disagreed_with_judge"),
            (previous.get("layer3_adjudication") or {}).get("disagreed_with_judge"),
        ),
    ]:
        if now is not None and before is not None:
            rows.append((name, now, before))

    print("\nComparison with the previous scorecard (this run - previous):")
    for name, now, before in rows:
        delta = now - before
        print(f"  {name:<26} {before:>10.4f} -> {now:>10.4f}   {delta:+.4f}")

    if current["contracts"] != previous["contracts"] or current["reviewer_model"] != previous["reviewer_model"]:
        print(
            f"\n  NOT LIKE FOR LIKE: {previous['contracts']} contracts on {previous['reviewer_model']} "
            f"versus {current['contracts']} on {current['reviewer_model']}."
        )


async def main_async(arguments) -> dict:
    settings = get_setting()
    eval_settings = get_eval_settings()
    categories = load_risk_categories()
    meter = TokenMeter()

    dataset = ensure_dataset(DATA_DIR)
    contracts = load_contracts(dataset)

    limit = len(contracts) if arguments.all else (arguments.limit or eval_settings.sample_size)
    sample = stratified_sample(contracts, limit, arguments.seed or eval_settings.sample_seed, categories)

    extraction_categories = None if arguments.all_categories else set(categories.risky)

    if arguments.dry_run:
        results_dir = arguments.replay or latest_run()
        if results_dir is None:
            raise SystemExit("nothing to re-score: no run found under evaluation/results/")

        recorded = {record["document"]: result_from_dict(record) for record in _read_jsonl(results_dir / "reviews.jsonl")}
        sample = [contract for contract in contracts if contract.title in recorded]
        reviews = [recorded[contract.title] for contract in sample]
        attempts = [ExtractionAttempt.from_dict(record) for record in _read_jsonl(results_dir / "extraction.jsonl")]
        verdicts = [JudgeVerdict.from_dict(record) for record in _read_jsonl(results_dir / "judge.jsonl")]
    else:
        check_distinct_models(settings, eval_settings, arguments)

        results_dir = RESULTS_DIR / f"cuad-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        results_dir.mkdir(parents=True, exist_ok=True)
        await meter.load_prices()

        logger.info("reviewing %d contract(s)", len(sample))
        reviews = await run_reviews(sample, meter, eval_settings.request_concurrency)
        _write_jsonl(results_dir / "reviews.jsonl", [review.to_dict() for review in reviews])

        attempts = []
        if not arguments.no_extraction:
            logger.info("running the CUAD extraction task")
            attempts = await run_extraction(sample, extraction_categories, meter, eval_settings.request_concurrency)
            _write_jsonl(results_dir / "extraction.jsonl", [attempt.to_dict() for attempt in attempts])

        verdicts = []

    risk_score, records = score_risk_recall(list(zip(sample, reviews, strict=True)), categories)
    _write_jsonl(results_dir / "risks.jsonl", [record.to_dict() for record in records])

    if not arguments.dry_run and not arguments.no_judge:
        findings = findings_to_judge(records)
        logger.info("judging %d finding(s) with %s", len(findings), eval_settings.judge_model)
        judge_llm = meter.llm(
            settings,
            model=eval_settings.judge_model,
            temperature=eval_settings.judge_temperature,
            max_tokens=eval_settings.judge_max_tokens,
        )
        try:
            verdicts = await judge_all(judge_llm, findings, eval_settings.request_concurrency)
        finally:
            await judge_llm.aclose()
        _write_jsonl(results_dir / "judge.jsonl", [verdict.to_dict() for verdict in verdicts])

    queue = build_queue(verdicts, categories.high_stakes, eval_settings.escalate_below, eval_settings.escalate_above)
    queue_path = results_dir / "escalations.jsonl"
    if queue_path.is_file():
        queue_path.unlink()
    write_queue(queue, queue_path)

    # layer 3: the expert model settles what it can, and the queue a lawyer works
    # through is what is left. On a dry run the recorded adjudications are read
    # back rather than paid for again.
    adjudications: list[Adjudication] = []
    if arguments.dry_run:
        adjudications = load_adjudications(results_dir)
    elif queue and not arguments.no_adjudicator:
        logger.info("adjudicating %d escalation(s) with %s", len(queue), eval_settings.adjudicator_model)
        expert_llm = meter.llm(
            settings,
            model=eval_settings.adjudicator_model,
            temperature=eval_settings.adjudicator_temperature,
            max_tokens=eval_settings.adjudicator_max_tokens,
        )
        try:
            adjudications = await adjudicate_all(
                expert_llm,
                read_queue(queue_path),
                eval_settings.request_concurrency,
                arguments.adjudicate_limit or eval_settings.adjudicate_limit,
            )
        finally:
            await expert_llm.aclose()
        write_adjudications(adjudications, results_dir / ADJUDICATIONS_FILE)

    for_humans = human_queue(read_queue(queue_path), adjudications)
    _write_jsonl(results_dir / "human_queue.jsonl", for_humans)

    extraction_score = score_extraction(attempts) if attempts else None

    report = {
        "suite": "cuad",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dry_run": bool(arguments.dry_run),
        "contracts": len(sample),
        "contract_titles": [contract.title for contract in sample],
        "reviewer_model": reviewer_model(settings),
        "judge_model": eval_settings.judge_model if not arguments.no_judge else "",
        "adjudicator_model": eval_settings.adjudicator_model if adjudications else "",
        "sample_seed": arguments.seed or eval_settings.sample_seed,
        "dataset": ATTRIBUTION,
        "layer1a_extraction": extraction_score.to_dict() if extraction_score else None,
        "layer1b_risk_recall": risk_score.to_dict(),
        "layer2_judge": summarize(verdicts) if verdicts else None,
        "escalations": len(queue),
        "layer3_adjudication": summarize_adjudications(adjudications) if adjudications else None,
        "awaiting_a_lawyer": len(for_humans),
        "layer3_agreement": agreement_report(load_reviews(results_dir)) if load_reviews(results_dir) else None,
        "end_to_end": end_to_end_miss_rate(risk_score, verdicts, queue, adjudications, len(for_humans)),
        "usage": {**meter.report(), "summary": meter.summary_line()},
        "results_dir": str(results_dir),
    }

    (results_dir / "scorecard.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CUAD evaluation end to end.")
    parser.add_argument("--limit", type=int, help="how many contracts to sample (default EVAL_SAMPLE_SIZE, 25)")
    parser.add_argument("--all", action="store_true", help="every contract in CUAD; expensive, read the estimate first")
    parser.add_argument("--seed", type=int, help="override the sampling seed")
    parser.add_argument("--dry-run", action="store_true", help="re-score a recorded run; makes no model calls")
    parser.add_argument("--replay", type=Path, help="which results directory --dry-run reads (default: the newest)")
    parser.add_argument("--compare", type=Path, help="a previous scorecard.json to diff this run against")
    parser.add_argument("--no-judge", action="store_true", help="skip layer 2, and layer 3 with it")
    parser.add_argument("--no-adjudicator", action="store_true", help="skip layer 3's expert model; queue everything for a human")
    parser.add_argument("--adjudicate-limit", type=int, default=0, help="adjudicate only the worst N escalations")
    parser.add_argument("--no-extraction", action="store_true", help="skip layer 1a, the expensive per-category task")
    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="run layer 1a over all 41 CUAD categories rather than the risky ones; 41 calls per contract",
    )
    parser.add_argument("--json", action="store_true", help="print the whole scorecard as JSON")
    arguments = parser.parse_args()

    report = asyncio.run(main_async(arguments))

    if arguments.json:
        print(json.dumps(report, indent=2))
    else:
        print_scorecard(report)

    if arguments.compare:
        compare(report, json.loads(arguments.compare.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
