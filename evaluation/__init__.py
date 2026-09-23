"""Three layers of evaluation for the legal review: metrics, a judge, a human.

Nothing here is imported by the service. The suite reads the product's own
modules — the parser, the batching, the prompts, the evidence check — so that a
score measures the pipeline that actually runs rather than a copy of it that
drifted.

    uv run python -m evaluation.fixtures.run   # seconds, free, every prompt change
    uv run python -m evaluation.run --limit 25 # CUAD, costs money, periodically
"""
