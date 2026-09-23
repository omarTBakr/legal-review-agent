"""
The project-wide risk register.

No model and no new judgement: this is the stored advice read a different way.
What is worth pinning down is which reviews it counts — a project with four
rounds of one contract must not report the same liability cap four times — and
that the order it puts risks in is the order someone with five minutes needs.
"""

import json

import pytest
from fastapi.testclient import TestClient

from enums.ReviewDecision import ReviewDecision
from enums.RiskSeverity import RiskSeverity
from main import app
from schemas.project import ProjectReview
from utils.advice_store import advice_key
from utils.projects import create_project, record_review
from utils.register import RegisterEntry, build_register, current_reviews, sort_entries


@pytest.fixture
def client(s3):
    return TestClient(app)


def store(s3, settings, pdf_key, risks):
    """Finished advice in the bucket, the way upload_advice would leave it."""
    document = {
        "task_id": "t",
        "pdf_key": pdf_key,
        "summary": "A contract.",
        "key_risks": [
            {
                "description": description,
                "severity": severity,
                "location": "",
                "quote": quote,
                "page": 1,
                "quote_verified": verified,
            }
            for description, severity, quote, verified in risks
        ],
        "review_decision": ReviewDecision.AUTO_APPROVED.value,
        "question": "",
    }
    s3.objects[(settings.s3_projects, advice_key(pdf_key))] = json.dumps(document).encode()


def entry(severity: str, pdf_key: str = "a.pdf", submitted_at: str = "2026-01-01") -> RegisterEntry:
    return RegisterEntry(
        task_id="t",
        pdf_key=pdf_key,
        description="x",
        severity=severity,
        submitted_at=submitted_at,
    )


class TestCurrentReviews:
    def test_a_superseded_round_is_dropped(self):
        reviews = [
            ProjectReview(task_id="r1", workflow_id="w1"),
            ProjectReview(task_id="r2", workflow_id="w2", supersedes="r1"),
        ]

        assert [review.task_id for review in current_reviews(reviews)] == ["r2"]

    def test_only_the_last_round_of_a_chain_survives(self):
        reviews = [
            ProjectReview(task_id="r1", workflow_id="w"),
            ProjectReview(task_id="r2", workflow_id="w", supersedes="r1"),
            ProjectReview(task_id="r3", workflow_id="w", supersedes="r2"),
        ]

        assert [review.task_id for review in current_reviews(reviews)] == ["r3"]

    def test_reviews_that_supersede_nothing_are_all_current(self):
        """Everything submitted before rounds existed."""
        reviews = [ProjectReview(task_id="a", workflow_id="w"), ProjectReview(task_id="b", workflow_id="w")]

        assert len(current_reviews(reviews)) == 2

    def test_a_link_to_a_review_in_another_project_keeps_both(self):
        reviews = [ProjectReview(task_id="r2", workflow_id="w", supersedes="elsewhere")]

        assert len(current_reviews(reviews)) == 1


class TestSorting:
    def test_worst_severity_comes_first(self):
        entries = [entry("low"), entry("critical"), entry("medium"), entry("high")]
        sort_entries(entries)

        assert [item.severity for item in entries] == ["critical", "high", "medium", "low"]

    def test_within_a_severity_the_newest_review_comes_first(self):
        entries = [
            entry("high", "old.pdf", "2026-01-01"),
            entry("high", "new.pdf", "2026-06-01"),
        ]
        sort_entries(entries)

        assert [item.pdf_key for item in entries] == ["new.pdf", "old.pdf"]

    def test_the_order_is_stable_for_identical_risks(self):
        """Two runs of the same project must not shuffle the list."""
        entries = [entry("high", "b.pdf", "2026-01-01"), entry("high", "a.pdf", "2026-01-01")]
        sort_entries(entries)

        assert [item.pdf_key for item in entries] == ["a.pdf", "b.pdf"]


async def test_the_register_gathers_every_document(s3, settings):
    project = create_project("Acme", settings)
    first, second = f"{project.prefix}one.pdf", f"{project.prefix}two.pdf"

    store(s3, settings, first, [("unlimited liability", "critical", "q1", True)])
    store(s3, settings, second, [("auto renewal", "medium", "q2", True), ("audit rights", "low", "q3", False)])

    reviews = [
        ProjectReview(task_id="t1", workflow_id="w", pdf_keys=[first], submitted_at="2026-01-01"),
        ProjectReview(task_id="t2", workflow_id="w", pdf_keys=[second], submitted_at="2026-01-02"),
    ]

    built = await build_register(project.id, reviews, settings)

    assert built.documents == 2
    assert [item.severity for item in built.entries] == ["critical", "medium", "low"]
    assert built.counts == {"critical": 1, "high": 0, "medium": 1, "low": 1}
    assert built.unverified == 1


async def test_a_superseded_round_is_not_counted_twice(s3, settings):
    project = create_project("Acme", settings)
    first, second = f"{project.prefix}round1.pdf", f"{project.prefix}round2.pdf"

    store(s3, settings, first, [("unlimited liability", "critical", "q", True)])
    store(s3, settings, second, [("unlimited liability", "critical", "q", True)])

    reviews = [
        ProjectReview(task_id="t1", workflow_id="w", pdf_keys=[first]),
        ProjectReview(task_id="t2", workflow_id="w", pdf_keys=[second], supersedes="t1"),
    ]

    built = await build_register(project.id, reviews, settings)

    assert len(built.entries) == 1
    assert built.superseded_reviews == 1
    assert built.entries[0].pdf_key == second


async def test_including_superseded_rounds_shows_both(s3, settings):
    project = create_project("Acme", settings)
    first, second = f"{project.prefix}round1.pdf", f"{project.prefix}round2.pdf"

    store(s3, settings, first, [("unlimited liability", "critical", "q", True)])
    store(s3, settings, second, [("unlimited liability", "critical", "q", True)])

    reviews = [
        ProjectReview(task_id="t1", workflow_id="w", pdf_keys=[first]),
        ProjectReview(task_id="t2", workflow_id="w", pdf_keys=[second], supersedes="t1"),
    ]

    built = await build_register(project.id, reviews, settings, include_superseded=True)

    assert len(built.entries) == 2


async def test_a_minimum_severity_drops_the_rest(s3, settings):
    project = create_project("Acme", settings)
    key = f"{project.prefix}one.pdf"

    store(s3, settings, key, [("bad", "critical", "q1", True), ("minor", "low", "q2", True)])
    reviews = [ProjectReview(task_id="t", workflow_id="w", pdf_keys=[key])]

    built = await build_register(project.id, reviews, settings, minimum=RiskSeverity.HIGH)

    assert [item.severity for item in built.entries] == ["critical"]
    # the counts describe what was asked for, so nothing is missing from them
    assert built.counts["low"] == 0


async def test_a_document_with_no_advice_is_pending_not_missing(s3, settings):
    project = create_project("Acme", settings)
    done, running = f"{project.prefix}done.pdf", f"{project.prefix}running.pdf"

    store(s3, settings, done, [("bad", "high", "q", True)])
    reviews = [ProjectReview(task_id="t", workflow_id="w", pdf_keys=[done, running])]

    built = await build_register(project.id, reviews, settings)

    assert built.pending == [running]
    assert built.documents == 1


def test_the_route_reports_the_register(client, s3, settings):
    project = create_project("Acme", settings)
    key = f"{project.prefix}one.pdf"
    store(s3, settings, key, [("unlimited liability", "critical", "q", True)])
    record_review(project.id, "t", "legal-review-t", [key], settings)

    body = client.get(f"/projects/{project.id}/register").json()

    assert body["risk_count"] == 1
    assert body["project_name"] == "Acme"
    assert body["risks"][0]["severity"] == "critical"


def test_the_route_filters_by_severity(client, s3, settings):
    project = create_project("Acme", settings)
    key = f"{project.prefix}one.pdf"
    store(s3, settings, key, [("bad", "critical", "q1", True), ("minor", "low", "q2", True)])
    record_review(project.id, "t", "legal-review-t", [key], settings)

    body = client.get(f"/projects/{project.id}/register", params={"minimum": "high"}).json()

    assert body["risk_count"] == 1


def test_an_unknown_severity_is_422(client, s3, settings):
    project = create_project("Acme", settings)

    response = client.get(f"/projects/{project.id}/register", params={"minimum": "catastrophic"})

    assert response.status_code == 422


def test_a_missing_project_is_404(client):
    assert client.get("/projects/no-such-project/register").status_code == 404
