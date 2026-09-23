"""
The current workflow code, replayed against histories of real runs.

Temporal does not store a workflow's state; it stores what happened and replays
the code to rebuild it. A worker picking up a review that started before a
deploy replays it against the *new* code, and a change that reorders, adds or
removes a step makes that replay diverge — the workflow fails mid-flight,
usually the one that has been waiting on a human for an hour.

These tests are the guard. The histories in `tests/histories/` were captured
from finished runs with `scripts/capture_history.py`; replaying them needs no
server, no network and no credentials, so they run in the normal suite.

**When one of these fails**, the question is not "how do I make the test pass"
but "what happens to the reviews already running". Usually the answer is
`workflow.patched("...")`: the new path for new runs, the old one for histories
that remember it.
"""

import base64
import binascii
import json
from contextlib import suppress
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from workflows import ALL_WORKFLOWS

HISTORIES = Path(__file__).resolve().parent / "histories"
FILES = sorted(HISTORIES.glob("*.json"))


def load(path: Path) -> WorkflowHistory:
    return WorkflowHistory.from_json(path.stem, path.read_text())


def test_there_are_histories_to_replay():
    """An empty directory would make every test below pass by doing nothing."""
    assert FILES, f"no histories in {HISTORIES}; capture one with scripts/capture_history.py"


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
async def test_the_current_code_replays(path):
    """A review that started before this change must survive it."""
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(load(path))


def decoded_payloads(events) -> str:
    """Everything the activities passed each other, as text."""
    text = []

    def walk(node):
        if isinstance(node, dict):
            if "data" in node and isinstance(node["data"], str):
                with suppress(ValueError, binascii.Error, UnicodeDecodeError):
                    text.append(base64.b64decode(node["data"]).decode("utf-8", "ignore"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(events)

    return "\n".join(text)


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_the_history_carries_no_client_text(path):
    """
    These files live in the repository, and a legal review's activity inputs
    carry the documents' text — base64 in the JSON, but text all the same.
    Only the invented contracts in testingDocs belong here.
    """
    payloads = decoded_payloads(json.loads(path.read_text())["events"])

    assert "Northwind" in payloads or "Acme" in payloads, (
        f"{path.name} does not look like a testingDocs run; a history captured from a real review "
        "would put a client's document text in the repository"
    )


async def test_every_history_names_the_workflow_it_replays():
    """A history for a workflow nobody registered would pass silently."""
    registered = {workflow.__name__ for workflow in ALL_WORKFLOWS}

    for path in FILES:
        events = json.loads(path.read_text())["events"]
        started = next(event for event in events if "workflowExecutionStartedEventAttributes" in event)
        name = started["workflowExecutionStartedEventAttributes"]["workflowType"]["name"]

        assert name in registered, f"{path.name} replays {name}, which is not registered"
