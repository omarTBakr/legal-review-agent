"""Layer 1: two numbers that need no judge and no human.

(a) extraction.py — how well the model does CUAD's own task.
(b) risk_recall.py — how much of CUAD's risky ground truth our review surfaces.

They are reported separately and never averaged together. (a) is a benchmark
number about a model; (b) is a product number about a pipeline. A run can move
one without moving the other, and that difference is usually the interesting
finding.
"""
