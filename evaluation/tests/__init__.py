"""Offline tests for the evaluation itself.

    uv run pytest evaluation

No network, no model, no credentials, no CUAD download. Everything is a small
inline fixture: what is being tested is the arithmetic and the rules, and a test
that needed a corpus would not be run often enough to catch anything.
"""
