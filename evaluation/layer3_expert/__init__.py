"""Layer 3: the expert. A big model settles what it can, a lawyer settles the rest.

The escalation queue from layer 2 goes to `adjudicator`, which re-decides each
finding with the strongest model on the list and says which ones still turn on
facts a lawyer has to bring. What it could not settle lands in
`human_queue.jsonl` for `review`, and `agreement` asks whether either model
reached the lawyer's conclusion.

uv run python -m evaluation.layer3_expert.adjudicator evaluation/results/cuad-<run>
uv run python -m evaluation.layer3_expert.review evaluation/results/cuad-<run>
uv run python -m evaluation.layer3_expert.agreement evaluation/results/cuad-<run>
"""
