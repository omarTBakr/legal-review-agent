# Evaluating the legal review

Three layers, because no one of them is enough. Automated metrics can tell you a
quote overlapped an annotated clause; they cannot tell you the risk was the right
risk. A judge model can say that, and cannot be trusted on the clauses where
being wrong is expensive. A human can, and does not scale. So: metrics on
everything, a judge on what the metrics matched, and a human on what the judge
was unsure about.

Nothing here is imported by the service. The suite calls the product's own
parser, batching, prompts, `LegalAdvice.from_model` and `utils/evidence.py`, so a
change to `prompts/legal_advice.py` moves the numbers. It is not a second copy of
the pipeline that quietly stopped matching.

## Quick start

```bash
# every prompt change: two invented contracts, seconds, cents
uv run python -m evaluation.fixtures.run

# periodically: CUAD, 25 contracts, a fixed stratified sample
uv run python -m evaluation.run --limit 25

# re-score the last run after changing a metric — free, no model calls
uv run python -m evaluation.run --dry-run

# this run against a previous scorecard
uv run python -m evaluation.run --compare evaluation/results/cuad-<earlier>/scorecard.json

# work through the escalation queue, then see whether the judge agreed
uv run python -m evaluation.layer3_human.review evaluation/results/cuad-<run>/escalations.jsonl
uv run python -m evaluation.layer3_human.agreement evaluation/results/cuad-<run>

# the suite's own tests: offline, no credentials, no download
uv run pytest evaluation
```

`--all` runs all 510 contracts. Read the cost estimate at the bottom of a
`--limit 25` run before you do.

## Settings

Everything is prefixed `EVAL_` and read from the same `.env`:

| Setting | Default | What it does |
| --- | --- | --- |
| `EVAL_JUDGE_MODEL` | `anthropic/claude-sonnet-5` | Must differ from `OPENROUTER_MODEL`; the run refuses otherwise |
| `EVAL_JUDGE_TEMPERATURE` | `0.0` | A rubric wants determinism |
| `EVAL_JUDGE_MAX_TOKENS` | `4000` | A reasoning judge spends most of this before writing any JSON |
| `EVAL_EXTRACTION_MODEL` | *(empty)* | Empty means the reviewer's own model |
| `EVAL_SAMPLE_SIZE` | `25` | Contracts drawn when `--limit` is not given |
| `EVAL_SAMPLE_SEED` | `20260101` | Change it and you are measuring a different sample |
| `EVAL_PAGE_CHARACTERS` | `2500` | Characters per synthetic page when paginating CUAD text |
| `EVAL_ESCALATE_ABOVE` / `_BELOW` | `0.3` / `0.8` | The unclear-score band that goes to a human |
| `EVAL_REQUEST_CONCURRENCY` | `4` | Model calls in flight at once |

## Layer 1 — automated metrics

Two measurements. **They are reported separately and never averaged**, because
one is a benchmark number about a model and the other is a product number about a
pipeline. A run can move either without moving the other, and that gap is usually
the interesting finding.

### 1a — CUAD-shaped extraction

One call per (contract, category): "return the spans for category X, or abstain".
Scored at token-set Jaccard ≥ 0.5 against the annotated spans; precision, recall
and F1 per category and overall.

**What this is comparable to, exactly.** The published DeBERTa-xlarge baseline
reports **AUPR 47.8** and **Precision@80%Recall 44.0**. Both need a ranked
confidence over candidate spans. A generative model asked to list spans gives no
ranking, so what comes out of 1a is precision/recall/F1 **at a single operating
point**. It is the same task, the same data and the same matching rule, and it is
**not** the paper's AUPR. Quoting our F1 beside their 47.8 would be wrong.

By default 1a runs over the risky categories only (29 of 41), because 41 calls per
contract over a 33,000-character contract is real money. `--all-categories` does
all 41; the overall F1 is only comparable between runs that used the same set.

### 1b — risk recall, the product metric

The *real* review runs — parse, batch, `prompts/legal_advice.py`, merge,
`verify_risks` — and then: of the CUAD spans in the risky categories, how many did
some risk's quote cover at Jaccard ≥ 0.5?

Three places this could flatter the system, and what is done instead:

**Precision.** CUAD annotates 41 categories. A risk we flag outside them — an
indemnity, a blank governing law, a 90-day payment term — is not wrong, it is
invisible to the benchmark. Those are `off_benchmark_findings`: counted and
listed, never false positives. The precision that *is* reported is
`precision_lower_bound`, with every off-benchmark finding still in the
denominator, and it is named a lower bound because that is what it is.

**Ranking.** The review has no confidence knob, so there is no threshold to
sweep. Severity is the ranking instead: critical, then critical+high, and so on.
**Four operating points.** The area under them is `aupr_by_severity` and **is not
comparable to the paper's AUPR**, which comes from a continuous score. It exists
so two of our own runs can be compared. `risks.jsonl` keeps every risk with its
severity and its match, so the day the prompt returns a real confidence this can
be rescored without paying for the reviews again.

**Quotes.** A risk whose quote the evidence check could not find still counts as
covering a span if the text matches — recall is about whether the clause was
surfaced. `unverified_coverage` says how many covered spans rest on an unverified
quote. That is the number to watch: coverage a reviewer cannot trust.

`evaluation/cuad/risk_categories.json` is the curated map from CUAD's categories
to what our review calls them, with `risky`, `high_stakes` and a one-line note
explaining each judgement. It is a judgement call and it is meant to be argued
with; every entry carries its reason so you can.

## Layer 2 — the judge

A stronger model grades each matched finding against the annotated clause on four
dimensions, 0 or 1 each: CORRECTNESS, COMPLETENESS, PRECISION, EXPLANATION.

- The judge **must** differ from the reviewer. A model grading its own output
  agrees with itself and the agreement is not evidence, so the run refuses to
  start when `EVAL_JUDGE_MODEL` equals `OPENROUTER_MODEL`.
- `temperature=0`.
- The reply is parsed with `LLMInterface.parse_json_object` — the product's own
  parser, fences, repair and all.
- `total_score` is **recomputed** from the four dimensions, not taken from the
  reply. Judges do arithmetic badly. The claimed value is kept as
  `reported_total`, and `arithmetic_disagreements` counts how often they differed.
- The clause and the system output are **untrusted data**. The rubric says so and
  tells the judge to note any instruction found inside them rather than follow it.
  A contract is an uploaded file, so this is a real path, not a hypothetical.

**Escalation** — any one of these sends a verdict to a human:

- an unclear score, `0.3 < total_score < 0.8`. Below 0.3 the finding is plainly
  wrong and a human confirming it adds nothing;
- a hedged `reason` — the rubric asks the judge to say "uncertain" or "borderline"
  when unsure, and a judge that does should be believed;
- a high-stakes category — liability, IP, termination, change of control, per
  `risk_categories.json`. A confident judge is still a model;
- a judge call that failed. There is no score to be confident about.

All the reasons are recorded, not just the first, so whoever triages the queue can
see that a borderline liability clause is not the same item as a borderline audit
right.

## Layer 3 — the human

`review.py` is a terminal form over `escalations.jsonl`. Per item it shows the
annotated clause, then the system output, then the judge's verdict — in that
order, because a reviewer who reads the grade first tends to agree with it — and
asks six questions: risk correctly identified (yes/no/partially), span accuracy
(exact/too broad/too narrow/wrong location), severity (correct/overstated/
understated), explanation legally sound, would you flag it to a client, and free
text. Answers append to `human_reviews.jsonl`; a session can be interrupted and
resumed, and a second reviewer's answers sit beside the first's.

`agreement.py` reduces both sides to one bit — did this finding pass? — and
reports raw agreement plus the two directions of disagreement separately.

**On Cohen's κ.** It measures agreement between *two* annotators, correcting for
what two coin flips with the same bias would reach by chance. With one human there
is no second annotator's marginal distribution to correct against, and computing
it against the judge's marginals would be measuring the judge against itself.
`cohens_kappa` returns `None` with a printed explanation until two humans have
reviewed overlapping items. Raw agreement is reported in the meantime and is
labelled "not chance-corrected", because it is not.

## `evaluation/fixtures` — the fast, free suite

Two invented contracts in `testingDocs/`, and `expected.json`: the table from
`testingDocs/README.md` made machine-readable — clause, page, expected severity
band, the words that must appear for it to count as found, and the sentence the
risk should be quoting.

Three numbers, because they fail for different reasons:

- **recall** — was the clause found at all, by keyword;
- **severity agreement within one band** — catches the failure the testingDocs
  README names, everything coming back `medium`;
- **quote verification rate** — catches the model paraphrasing instead of copying,
  which no keyword test would notice.

`anchor_hit_rate` sits beside recall: found the clause *and* quoted the right
sentence. High recall with low anchor hits means the review is describing the
right risks off the wrong evidence.

`must_include` is groups of alternatives — a risk counts when every group has one
hit — because the model will say "uncapped" where the table says "unlimited", and
that is phrasing, not a miss. Anything the review flags that no expectation covers
is `extra_findings`: counted, never a false positive. The testingDocs README says
outright that the model may reasonably flag things the table does not.

One expectation, the blank governing law in clause 12.1, carries
`satisfied_by_needs_human`. The design says to *ask* about it rather than guess,
so a review that raises it through `needs_human` has done the right thing and is
credited for it. It contributes to recall and to nothing else, since a question
has no severity or quote to score.

This is the suite to run on every prompt change. CUAD is periodic. It also covers
the one stage CUAD cannot: the real PDF parser.

## Cost

Every run prints token counts and an estimated cost. The tokens are the
**provider's own**, read out of OpenRouter's `usage` block by an httpx response
hook, not estimated from character counts. Prices come from OpenRouter's public
model list at the start of each run; a model the list does not mention is counted
and named under `unpriced_models` rather than costed at zero. "Estimated" because
OpenRouter's invoice applies discounts, cache hits and rounding this does not
know about.

At the defaults, on `deepseek/deepseek-v4-flash` reviewing and
`anthropic/claude-sonnet-5` judging, projected from the smoke run's measured
per-call tokens and the real shape of the seed-20260101 sample of 25 (1.27M
characters of contract, 282 risky annotated spans):

| Stage | Model calls | Projected cost |
| --- | --- | --- |
| `fixtures.run` | 2 | ~$0.01 |
| `run --limit 25`, the reviews | 34 `legal_advice` + 5 merges | ~$0.05 |
| `run --limit 25`, layer 1a (risky categories) | 725 | ~$0.87 |
| `run --limit 25 --all-categories`, layer 1a | 1,025 | ~$1.25 |
| `run --limit 25`, layer 2 | one per matched finding, 282 at perfect recall | $1.50–$3.20 |
| **`run --limit 25` all in** | ~1,050 | **~$2.50–$4.10** |
| `run --all --all-categories` | ~21,000 extraction calls alone | 20× that. Don't, without a budget. |

The judge dominates, so `--no-judge` and `--no-extraction` are the two knobs that
matter. A cheaper `EVAL_JUDGE_MODEL` is the other, at the cost of the judge being
weaker than the model it grades, which defeats the point.

Raw model output lands in `evaluation/results/<timestamp>/` — `reviews.jsonl`,
`extraction.jsonl`, `risks.jsonl`, `judge.jsonl`, `escalations.jsonl`,
`scorecard.json` — so re-scoring after a metric change costs nothing. Both
`evaluation/data/` and `evaluation/results/` are gitignored.

## The headline number

**End-to-end miss rate**: risky CUAD clauses that no layer caught. A clause counts
as caught when the review surfaced it *and* the judge did not score the finding 0
— telling a client about the wrong risk in the right clause leaves them exposed in
the same way as saying nothing. `pending_human` says how many catches rest on a
verdict a human has not seen yet, so the number is provisional until layer 3 has
run.

## The dataset

CUAD v1 (Contract Understanding Atticus Dataset), The Atticus Project, licensed
**CC BY 4.0**. <https://www.atticusprojectai.org/cuad> · Hendrycks, Burns, Chen
and Ball, *CUAD: An Expert-Annotated NLP Dataset for Legal Contract Review*,
arXiv:2103.06268.

510 commercial contracts, 41 clause categories, 13,000+ annotated spans.
Downloaded on demand from the project's own repository — an 18 MB archive whose
SHA-256 is pinned in `evaluation/cuad/download.py` and verified before anything is
extracted. Only `CUADv1.json` is kept: the contracts' plain text is inside it, so
the 100 MB of PDFs is never fetched. Nothing is committed.

## Known weaknesses

Read these before quoting any number.

1. **Contamination.** CUAD is public and has been since 2021. Any model trained
   after that has probably seen these contracts and possibly the annotations.
   Layer 1a in particular may be measuring recall of training data. The fixture
   suite uses invented contracts partly for this reason, and is the only number
   here that is definitely not contaminated.
2. **CUAD's framing is not ours.** It annotates clauses a lawyer should read in a
   corporate transaction. We ask what exposes a client. `risk_categories.json`
   bridges the two by hand, which is a judgement, and 12 of the 41 categories are
   marked not-risky and so contribute to no recall number.
3. **No indemnity category.** CUAD v1 has none, so every indemnity finding the
   review makes is off-benchmark and is never graded by layer 1 or layer 2. On a
   one-sided services agreement the indemnity is often the second-worst clause in
   the document. `expected.json` covers indemnities; CUAD cannot.
4. **CUAD cannot test the parser.** It ships plain text, so `paginate` stands in
   for `parse_pdf_pages` and pages are synthetic paragraph-boundary chunks. Every
   page number, and every quote-verification result, therefore comes from text
   that never went through pymupdf4llm. The fixture suite covers that stage; the
   CUAD numbers do not.
5. **`aupr_by_severity` is four points.** It is not AUPR. Neither is 1a's F1.
   Both are labelled in the code, in the JSON `note` fields and on the terminal
   output, and they will still get quoted wrongly.
6. **`precision_lower_bound` is genuinely a lower bound**, and the gap between it
   and real precision is exactly `off_benchmark_findings`. There is no honest way
   to close it without annotating the off-benchmark findings by hand, which is
   what layer 3 is for.
7. **Greedy one-to-one span matching** is not optimal assignment. A pathological
   set of overlapping predictions could be matched better than greedy manages.
   Jaccard ≥ 0.5 is also a blunt instrument: it will call a sentence plus a
   trailing proviso a match, and reject a correct quote that the annotator drew
   twice as long.
8. **The judge grades only matched findings**, because the rubric needs a clause
   to grade against. A review that surfaced nothing gets no judge verdicts and so
   no layer-2 signal at all — the miss shows up in 1b and nowhere else.
9. **One judge, one sample, no self-consistency check.** The judge is not run
   twice at temperature 0 to see whether it agrees with itself, and its pass rate
   is not validated against humans until someone works the queue. Until then
   layer 2's numbers are a model's opinion.
10. **The fixture expectations are keyword matching.** A review that says
    "liability is unlimited" about the wrong clause scores a hit on recall.
    `anchor_hit` is the check against that, and it is reported separately rather
    than folded in.
11. **The batches are enormous and the suite does not isolate that.** At
    `LEGAL_PAGES_PER_BATCH=30` and 2,500-character pages, one batch is ~75,000
    characters — the 338,000-character contract in the default sample becomes 5
    batches, each asking the model to find every risk in 30 pages inside one
    16,000-token reply. Recall almost certainly falls with contract length, and
    the strata make that visible in `reviews.jsonl` but no reported metric breaks
    recall down by batch count. It should.
