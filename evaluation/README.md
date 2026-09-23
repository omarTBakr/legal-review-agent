# Evaluating the legal review

Three layers, because no one of them is enough. Automated metrics can tell you a
quote overlapped an annotated clause; they cannot tell you the risk was the right
risk. A judge model can say that, and cannot be trusted on the clauses where
being wrong is expensive. A human can, and does not scale. So: metrics on
everything, a judge on what the metrics matched, and layer 3 on what the judge
was unsure about — first the strongest model available, then a lawyer on what even
that could not settle.

**A model per level, each stronger than the one below, none of them the same.**
The cost of a level falls as its model gets dearer, because each sees a fraction
of what the one beneath it did:

| Level | Setting | Default | Sees |
| --- | --- | --- | --- |
| Reviewer, the system under test | `OPENROUTER_MODEL` | `deepseek/deepseek-v4.1-flash` | every batch of every contract |
| Layer 2, the judge | `EVAL_JUDGE_MODEL` | `anthropic/claude-sonnet-5` | every finding that matched a clause |
| Layer 3, the expert | `EVAL_ADJUDICATOR_MODEL` | `anthropic/claude-opus-5` | only the escalations |

The run **refuses to start** when any two of them are the same model. A model
grading or overturning its own output agrees with itself, and the agreement is not
evidence — it is a number that looks like one, which is worse than no number.

## Running it on a local model

`LLM_PROVIDER=ollama` points the reviewer at a model on this machine, and the
per-call cost goes to zero — a sweep stops being a budget decision. Two things
to know before reading any number that comes out of it:

- **Set `OLLAMA_CONTEXT_TOKENS` to cover the batch.** Ollama does not use a
  model's full context by default; it truncates to its own much smaller one,
  silently. At `LEGAL_PAGES_PER_BATCH=30` a batch is ~21,000 tokens, so on the
  default the model reads the opening pages and reports no risks in the rest of
  a contract it never saw. Either raise the context or lower the batch size —
  and on a consumer GPU, lower the batch size.
- **Layers 2 and 3 still want a hosted model.** The rule that no two levels
  share a model is about independence, not about where they run, and a *weaker*
  judge grading a stronger reviewer produces a number that means the opposite of
  what it says (weakness 12 below). Run layer 1 locally with `--no-judge` unless
  you have a genuinely stronger local model to judge with.

```bash
LLM_PROVIDER=ollama LEGAL_PAGES_PER_BATCH=10 EVAL_REQUEST_CONCURRENCY=1 \
  uv run python -m evaluation.run --limit 25 --no-extraction --no-judge
```

`EVAL_REQUEST_CONCURRENCY=1` because one GPU runs one generation at a time;
asking for four in flight makes them queue and thrash rather than overlap.

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

# adjudicate a run's queue on its own (a run made with --no-adjudicator)
uv run python -m evaluation.layer3_expert.adjudicator evaluation/results/cuad-<run>

# work through what the expert left, then see whether either model agreed
uv run python -m evaluation.layer3_expert.review evaluation/results/cuad-<run>
uv run python -m evaluation.layer3_expert.agreement evaluation/results/cuad-<run>

# the suite's own tests: offline, no credentials, no download
uv run pytest evaluation
```

`--all` runs all 510 contracts. Read the cost estimate at the bottom of a
`--limit 25` run before you do.

## Settings

Everything is prefixed `EVAL_` and read from the same `.env`:

| Setting | Default | What it does |
| --- | --- | --- |
| `EVAL_JUDGE_MODEL` | `anthropic/claude-sonnet-5` | Layer 2. Must differ from the other two; the run refuses otherwise |
| `EVAL_JUDGE_TEMPERATURE` | `0.0` | A rubric wants determinism |
| `EVAL_JUDGE_MAX_TOKENS` | `4000` | A reasoning judge spends most of this before writing any JSON |
| `EVAL_ADJUDICATOR_MODEL` | `anthropic/claude-opus-5` | Layer 3's expert. Must differ from the other two |
| `EVAL_ADJUDICATOR_TEMPERATURE` | `0.0` | As the judge |
| `EVAL_ADJUDICATOR_MAX_TOKENS` | `6000` | It writes more than the judge, and reasons before it does |
| `EVAL_ADJUDICATE_LIMIT` | `0` | Cap the escalations adjudicated, worst score first; 0 is all |
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

- The judge **must** differ from the reviewer and from layer 3's expert. A model
  grading its own output agrees with itself and the agreement is not evidence, so
  the run refuses to start when any two levels name the same model.
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

## Layer 3 — the expert, then the human

### The expert model

`adjudicator.py` takes the escalation queue and re-decides each item with the
strongest model on the list. Only the queue reaches it — a few dozen items against
the judge's few hundred and the reviewer's thousands — which is what makes the
expensive model affordable here.

It is a third opinion, not a second judge:

- its answer is a **decision** on the finding — `upheld`, `partial`,
  `overturned` — not the judge's four bits;
- it is **given the judge's verdict** to disagree with. That risks anchoring it,
  which is why `review.py` shows a human the grade last, but withholding it would
  make this a second judge rather than a third level, and whether a stronger model
  overturns a weaker one's calls is the question layer 3 exists to answer;
- it answers one the judge cannot: **does this need a lawyer at all?** A finding
  that turns on the commercial bargain, the governing law or what the parties
  actually did is a question for someone with the file, and saying so is the
  useful answer. A finding the expert settles from the clause text is a finding a
  human does not have to read, which is the only way a human layer scales past a
  demo.

`needs_human` is forced true whatever the reply claimed when the expert's
confidence is `low`, when the call failed, or when the decision is not one of the
three the rubric allows — an adjudicator that invents a fourth verdict has not
followed the rubric, and mapping its invention onto the nearest real one would be
us deciding rather than it.

Two disagreements with the judge are counted separately, because they cost
different things: `judge_passed_expert_overturned` is a bad finding that reached a
client, `judge_failed_expert_upheld` is human time about to be spent on a finding
that was fine. One number for both would hide which mistake the judge is making.

What the expert could not settle lands in `human_queue.jsonl`, carrying its
decision, confidence and reason so the lawyer starts from an opinion rather than
from nothing. An item the expert never saw — `EVAL_ADJUDICATE_LIMIT` cut the queue
short, or `--no-adjudicator` — stays in that queue untouched. An unasked question
is not an answered one.

### The human

`review.py` is a terminal form over `human_queue.jsonl` (point it at the run
directory and it picks the right file; reviewing `escalations.jsonl` directly would
spend an afternoon on findings the expert already settled). Per item it shows the
annotated clause, then the system output, then the judge's verdict, then the
expert's — in that order, because a reviewer who reads a grade first tends to agree
with it — and asks six questions: risk correctly identified (yes/no/partially), span accuracy
(exact/too broad/too narrow/wrong location), severity (correct/overstated/
understated), explanation legally sound, would you flag it to a client, and free
text. Answers append to `human_reviews.jsonl`; a session can be interrupted and
resumed, and a second reviewer's answers sit beside the first's.

`agreement.py` reduces each side to one bit — did this finding pass? — and reports
raw agreement plus the two directions of disagreement, for the judge and for the
expert separately. The judge's agreement is measured over everything it graded;
the expert's only over the items it could *not* settle, since those are the ones a
human sees. The expert's number is therefore the harder test and will read lower.
It is also the number that says whether the dear model is earning its place.

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

At the defaults — `deepseek/deepseek-v4.1-flash` reviewing at $0.10/$0.50 per M
tokens, `anthropic/claude-sonnet-5` judging at $2/$10, `anthropic/claude-opus-5`
adjudicating at $5/$25 — projected from the smoke run's measured per-call tokens
and the real shape of the seed-20260101 sample of 25 (1.27M characters of contract,
282 risky annotated spans):

| Stage | Model calls | Projected cost |
| --- | --- | --- |
| `fixtures.run` | 2 | ~$0.02 |
| `run --limit 25`, the reviews | 34 `legal_advice` + 5 merges | ~$0.15 |
| `run --limit 25`, layer 1a (risky categories) | 725 | ~$1.05 |
| `run --limit 25 --all-categories`, layer 1a | 1,025 | ~$1.50 |
| `run --limit 25`, layer 2 | one per matched finding, 282 at perfect recall | $1.50–$3.20 |
| `run --limit 25`, layer 3 | one per escalation, expect 60–90 | $2.00–$5.00 |
| **`run --limit 25` all in** | ~1,150 | **~$5–$10** |
| `run --all --all-categories` | ~21,000 extraction calls alone | 20× that. Don't, without a budget. |

Layer 1a is most of the calls and almost none of the money; layers 2 and 3 are the
reverse. The knobs, in the order worth reaching for: `--no-extraction` (drops 725
calls for ~$1), `EVAL_ADJUDICATE_LIMIT` (spends layer 3 on the worst N escalations
only), `--no-adjudicator`, `--no-judge`. A cheaper judge or expert is the other
option, at the cost of each grading a model no weaker than itself, which defeats
the point.

Raw model output lands in `evaluation/results/<timestamp>/` — `reviews.jsonl`,
`extraction.jsonl`, `risks.jsonl`, `judge.jsonl`, `escalations.jsonl`,
`adjudications.jsonl`, `human_queue.jsonl`, `scorecard.json` — so re-scoring after a
metric change costs nothing: `--dry-run` reads the adjudications back rather than
paying for them again. Both `evaluation/data/` and `evaluation/results/` are
gitignored.

## The headline number

**End-to-end miss rate**: risky CUAD clauses that no layer caught. A clause counts
as caught when the review surfaced it *and* whoever looked hardest at the finding
agreed with it — telling a client about the wrong risk in the right clause leaves
them exposed in the same way as saying nothing.

Where layer 3 has spoken, it and not the judge decides: the expert saw the same
clause with more capacity, and letting it overturn the judge in both directions is
why it is there. `rescued_by_expert` counts clauses the judge failed and the expert
upheld, `overturned_by_expert` the reverse, and a `partial` is worth half a catch —
the only honest weight for "the right risk, half of it stated". So `caught` is
fractional once layer 3 has run, and says so by being a float. Where several
findings match one clause the best verdict among them wins: a clause is caught if
anything caught it.

`pending_human` is the length of `human_queue.jsonl` — catches resting on nobody's
judgement yet — so the headline stays provisional until a lawyer has worked it, and
the number cannot disagree with the queue the run actually wrote.

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
10. **The expert is anchored on purpose.** It is shown the judge's verdict, which
    is known to pull a grader towards agreement. The trade is deliberate — see the
    layer 3 section — but it means `disagreed_with_judge` is a floor, not an
    estimate: a blind adjudicator would disagree more. Running it both ways on the
    same queue would measure the size of the anchor, and nothing does that yet.
11. **Layer 3's model is still a model.** A bigger one settling a case a smaller
    one could not is not the same as being right, and until a lawyer works
    `human_queue.jsonl`, `expert_vs_human` is empty and the expert's decisions are
    an opinion that happens to be expensive. Contamination (1) applies to it as
    much as to the reviewer.
12. **Nothing checks the two models are actually different in strength**, only
    that their ids differ. Setting a weak adjudicator over a strong judge would run
    happily and produce a number that means the opposite of what it says.
13. **The fixture expectations are keyword matching.** A review that says
    "liability is unlimited" about the wrong clause scores a hit on recall.
    `anchor_hit` is the check against that, and it is reported separately rather
    than folded in.
14. **The batches are enormous and the suite does not isolate that.** At
    `LEGAL_PAGES_PER_BATCH=30` and 2,500-character pages, one batch is ~75,000
    characters — the 338,000-character contract in the default sample becomes 5
    batches, each asking the model to find every risk in 30 pages inside one
    16,000-token reply. Recall almost certainly falls with contract length, and
    the strata make that visible in `reviews.jsonl` but no reported metric breaks
    recall down by batch count. It should.
