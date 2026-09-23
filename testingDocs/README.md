# Testing documents

Two invented contracts for exercising the review end to end. Northwind Systems
Ltd is fictional, and the clauses are written to be one-sided in ways a review
should notice — this is a test fixture, not a model agreement, and nothing here
is legal drafting worth copying.

```bash
uv run python testingDocs/make_documents.py   # rebuild after editing a clause
```

| File | Pages | What it is for |
| --- | --- | --- |
| `services-agreement.pdf` | 4 | The main one: many risks, spread across pages, plus a gap only a human can fill |
| `mutual-nda.pdf` | 2 | A short second document, for testing a review of more than one at a time |

## What a good review finds

Not a scoring key — the model will word things its own way, and may reasonably
flag things this list does not. It is here so you can tell a real miss from a
difference of phrasing.

**services-agreement.pdf**

| Clause | Page | The problem |
| --- | --- | --- |
| 6.1 | 2 | The **client's** liability is unlimited, while the supplier's is capped at 10% of three months' fees (6.2). The asymmetry is the point. |
| 7.1, 7.2 | 3 | The client indemnifies the supplier, including for the supplier's own infringement; the supplier indemnifies nothing. |
| 9.1, 9.2 | 3 | The supplier may terminate for convenience; the client only for unremedied material breach — and 9.3 accelerates the whole remaining term. |
| 4.1 | 2 | IP in the deliverables vests in the **supplier**, including the client's own materials, and the licence back is revocable. |
| 3.2 | 1 | Thirty-six month term, automatic renewal, 180 days' notice to escape it. |
| 2.2, 2.3, 2.4 | 1 | Ninety-day payment terms, rates raised on 14 days' notice applying to scheduled work, and 8% interest **per month**. |
| 10.1, 10.2 | 4 | Two-year non-solicitation covering people who approach the client unprompted, with a twelve-month-salary penalty. |
| 11.1, 11.2 | 4 | The supplier may assign freely; the client may not assign at all. |
| 8.1, 8.2 | 3 | Personal data may go to any country the supplier operates in, and breach notice is "within a reasonable period". |
| 5.2 | 2 | The supplier may use the client's name in marketing without approval. |
| 12.1 | 4 | **Governing law is left blank** — `[JURISDICTION TO BE AGREED BY THE PARTIES]`. |

Clause 12.1 is the interesting one: the model cannot answer it from the
document, so it should set `needs_human` and ask. That is the path that pauses
the document, frees its slot, and waits for `POST /legal/{task_id}/respond` —
answer it in the UI and the advice is revised. Leave it unanswered past
`HUMAN_INPUT_TIMEOUT_SECONDS` and the document should finish anyway, marked
`unreviewed_timeout` and flagged as needing attention.

**mutual-nda.pdf**

| Clause | Page | The problem |
| --- | --- | --- |
| 2.2 | 1 | Confidentiality in perpetuity, surviving termination indefinitely. |
| 1.1 | 1 | "Confidential Information" covers unmarked information and things the recipient develops independently. |
| 2.3 | 1 | Certified destruction including routine backups, which is usually impossible to honour. |
| 3.2 | 1 | The recipient pays costs on an indemnity basis whether or not Northwind wins. |
| 4.2 | 2 | Feedback becomes Northwind's property. |
| 5.2, 6.2 | 2 | Only Northwind may terminate, and it may amend the terms unilaterally. |
| — | — | It is called *mutual* and is not; a review that says so is reading properly. |

## Also worth checking

- **Quotes and pages.** Every risk should carry a quote that really appears in
  the document, and the page number should match the table above. A risk marked
  "unverified quote" in the UI is the evidence check doing its job — the model
  paraphrased instead of copying.
- **Chat.** With both documents in one project, ask things that span them:
  "which of these two is worse for us?", "what is the notice period?", "is
  there anything about non-solicitation?". An answer should cite a page. Ask
  something the documents do not cover — "is there a data processing
  addendum?" — and it should say so rather than invent one.
- **Severity.** Clause 6.1 and 7.1 should outrank 5.2. If everything comes back
  `medium`, the prompt's severity guidance is not landing.
