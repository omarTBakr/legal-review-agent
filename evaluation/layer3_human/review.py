"""A terminal form over the escalation queue.

Shows one escalated finding at a time: the annotated clause, what the review
said, and how the judge graded it, then asks six questions. The answers append
to JSONL beside the queue, so an interrupted session keeps what it recorded and
a second reviewer's answers sit next to the first's rather than overwriting them.

Deliberately plain input(): a curses form would need a terminal to test and the
job here is to get a lawyer's judgement written down, not to be pleasant. The
judge's verdict is shown *after* the system output and before the questions, in
that order, because a reviewer who reads the grade first tends to agree with it.

    uv run python -m evaluation.layer3_human.review <escalations.jsonl> [--reviewer name]
"""

import argparse
import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path

RESPONSES_FILE = "human_reviews.jsonl"

# (field, question, {key: value}) — the structured form, in the order it is asked
QUESTIONS = (
    (
        "risk_identified",
        "Was the risk correctly identified?",
        {"y": "yes", "n": "no", "p": "partially"},
    ),
    (
        "span_accuracy",
        "Was the quoted span right?",
        {"e": "exact", "b": "too broad", "n": "too narrow", "w": "wrong location"},
    ),
    (
        "severity",
        "Was the severity right?",
        {"c": "correct", "o": "overstated", "u": "understated"},
    ),
    (
        "explanation_sound",
        "Was the explanation legally sound?",
        {"y": "yes", "n": "no", "p": "partially"},
    ),
    (
        "would_flag_to_client",
        "Would you flag this to a client?",
        {"y": "yes", "n": "no"},
    ),
)


def _wrap(text: str, width: int = 100, indent: str = "  ") -> str:
    """Soft-wraps a clause so a 3,000-character span does not fill the scrollback."""
    return "\n".join(
        textwrap.fill(line, width=width, initial_indent=indent, subsequent_indent=indent) for line in text.splitlines() or [""]
    )


def render(item: dict, position: int, total: int) -> str:
    """One queue item as the reviewer sees it."""
    lines = [
        "",
        "=" * 100,
        f"[{position} of {total}]  {item.get('document', '')}  /  {item.get('category', '')}",
        f"escalated because: {', '.join(item.get('escalation_reasons') or ['(unrecorded)'])}",
        "=" * 100,
        "",
        "GROUND TRUTH — the annotated clause:",
        _wrap(item.get("ground_truth", "")),
        "",
        "SYSTEM OUTPUT:",
        f"  description : {item.get('description', '')}",
        f"  severity    : {item.get('severity', '')}",
        f"  quote found : {item.get('quote_verified')}",
        "  quote       :",
        _wrap(item.get("quote", ""), indent="    "),
        "",
        "JUDGE:",
        f"  correctness {item.get('correctness')}  completeness {item.get('completeness')}  "
        f"precision {item.get('precision')}  explanation {item.get('explanation')}",
        f"  total {item.get('total_score')}  pass {item.get('pass')}",
        f"  reason: {item.get('reason', '')}",
        "",
    ]

    return "\n".join(lines)


def ask(question: str, options: dict[str, str], reader=input) -> str | None:
    """
    Asks one question until the answer is one of the options.

    Returns None when the reviewer types "s" to skip the whole item or "q" to
    stop: a form that forces an answer to a question the reviewer cannot answer
    gets a guess, and a guessed ground truth is worse than a gap.
    """
    prompt = "  ".join(f"[{key}] {value}" for key, value in options.items())

    while True:
        answer = reader(f"{question}\n  {prompt}  [s]kip item  [q]uit\n> ").strip().lower()
        if answer in options:
            return options[answer]
        if answer in ("s", "q"):
            return answer
        print(f"  '{answer}' is not one of {', '.join(options)} — try again.")


def collect(item: dict, reviewer: str, reader=input) -> dict | None:
    """
    Walks one item through the form. Returns None when it was skipped.

    "q" raises KeyboardInterrupt so the caller stops the session the same way it
    handles a ctrl-c, rather than needing a second exit path.
    """
    answers = {}

    for field, question, options in QUESTIONS:
        answer = ask(question, options, reader)
        if answer == "q":
            raise KeyboardInterrupt
        if answer == "s":
            return None
        answers[field] = answer

    answers["notes"] = reader("Notes (optional)\n> ").strip()

    return {
        "reviewer": reviewer,
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "document": item.get("document", ""),
        "category": item.get("category", ""),
        "quote": item.get("quote", ""),
        "judge_total_score": item.get("total_score"),
        "judge_pass": item.get("pass"),
        "escalation_reasons": item.get("escalation_reasons") or [],
        **answers,
    }


def already_reviewed(path: Path, reviewer: str) -> set[tuple[str, str, str]]:
    """
    What this reviewer has already answered, so a resumed session skips it.

    Keyed by (document, category, quote) rather than by position, because the
    queue is regenerated on every run and positions move.
    """
    if not path.is_file():
        return set()

    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("reviewer") == reviewer:
            done.add((record.get("document", ""), record.get("category", ""), record.get("quote", "")))

    return done


def run(queue_path: Path, reviewer: str, reader=input) -> Path:
    """Works through the queue, appending each completed form."""
    items = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    responses = queue_path.parent / RESPONSES_FILE
    done = already_reviewed(responses, reviewer)

    pending = [item for item in items if (item.get("document", ""), item.get("category", ""), item.get("quote", "")) not in done]

    if not pending:
        print(f"Nothing left in {queue_path} for {reviewer}.")
        return responses

    print(f"{len(pending)} of {len(items)} escalation(s) left for {reviewer}. [q] to stop; answers are saved as you go.")

    recorded = 0
    try:
        for position, item in enumerate(pending, start=1):
            print(render(item, position, len(pending)))
            answers = collect(item, reviewer, reader)
            if answers is None:
                continue
            with responses.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(answers) + "\n")
            recorded += 1
    except KeyboardInterrupt:
        print("\nStopped.")

    print(f"Recorded {recorded} review(s) in {responses}")

    return responses


def main() -> None:
    parser = argparse.ArgumentParser(description="Review the escalation queue from a run.")
    parser.add_argument("queue", type=Path, help="path to escalations.jsonl")
    parser.add_argument("--reviewer", default="", help="who is reviewing; asked for if omitted")
    arguments = parser.parse_args()

    reviewer = arguments.reviewer or input("Your name or initials: ").strip() or "anonymous"

    run(arguments.queue, reviewer)


if __name__ == "__main__":
    main()
