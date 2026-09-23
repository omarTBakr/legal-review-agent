"""Layer 2: a stronger model grades the review against the annotated clause.

Layer 1 can say a quote overlapped a span. It cannot say whether the risk was
the right risk, whether the severity was defensible, or whether the explanation
holds. That is what the rubric asks, and what gets escalated when the answer is
not clear.
"""
