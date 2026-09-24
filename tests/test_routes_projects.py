"""The /projects endpoints, against the in-memory S3 fake."""

import json

import pytest
from fastapi.testclient import TestClient

from enums.ReviewDecision import ReviewDecision
from enums.RiskSeverity import RiskSeverity
from main import app
from schemas.key_risk import KeyRisk
from schemas.legal_advice import LegalAdvice
from utils.advice_store import advice_key
from utils.projects import create_project, record_review


@pytest.fixture
def client(s3):
    return TestClient(app)


def store_advice(s3, settings, pdf_key, verified=True):
    """Puts finished advice in the bucket, the way upload_advice would."""
    advice = LegalAdvice(
        summary="A services agreement.",
        key_risks=[
            KeyRisk(
                description="Unlimited liability",
                severity=RiskSeverity.HIGH,
                location="clause 9",
                quote="The Supplier's liability is unlimited.",
                page=5,
                confidence=0.9,
                category="liability",
                recommended_action="Negotiate a liability cap.",
                quote_verified=verified,
            )
        ],
        review_decision=ReviewDecision.HUMAN_APPROVED,
    )
    document = {
        "schema_version": 2,
        "task_id": "abc123",
        "pdf_key": pdf_key,
        "summary": advice.summary,
        "key_risks": [risk.to_dict() for risk in advice.key_risks],
        "review_decision": advice.review_decision.value,
        "question": "",
    }
    s3.objects[(settings.s3_projects, advice_key(pdf_key))] = json.dumps(document).encode()

    return advice


# --- POST /projects ------------------------------------------------------


def test_creating_a_project_returns_201_and_its_folder(client):
    response = client.post("/projects", json={"name": "Acme NDAs", "description": "Standard NDAs"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme NDAs"
    assert body["prefix"] == f"{body['id']}/"


def test_the_folder_appears_in_the_bucket(client, s3, settings):
    body = client.post("/projects", json={"name": "Acme"}).json()

    assert (settings.s3_projects, f"{body['prefix']}project.json") in s3.objects


def test_a_description_and_email_are_optional(client):
    body = client.post("/projects", json={"name": "Acme"}).json()

    assert body["description"] == "" and body["email"] == ""


def test_a_project_without_a_name_is_rejected(client):
    assert client.post("/projects", json={"name": "  "}).status_code == 400


def test_a_bad_email_is_rejected(client):
    assert client.post("/projects", json={"name": "Acme", "email": "nope"}).status_code == 400


# --- GET /projects -------------------------------------------------------


def test_projects_are_listed(client, s3, settings):
    create_project("One", settings)
    create_project("Two", settings)

    body = client.get("/projects").json()

    assert body["project_count"] == 2
    assert {project["name"] for project in body["projects"]} == {"One", "Two"}


def test_an_empty_bucket_lists_no_projects(client):
    assert client.get("/projects").json() == {"projects": [], "project_count": 0}


# --- GET /projects/{id} --------------------------------------------------


def test_a_project_reports_its_reviews(client, s3, settings):
    project = create_project("Acme", settings)
    record_review(project.id, "abc123", "legal-review-abc123", [f"{project.prefix}a.pdf"], settings)

    body = client.get(f"/projects/{project.id}").json()

    assert body["name"] == "Acme"
    assert body["review_count"] == 1
    assert body["reviews"][0]["task_id"] == "abc123"


def test_an_unknown_project_is_a_404(client):
    assert client.get("/projects/nothing-here").status_code == 404


def test_a_project_id_that_could_escape_the_prefix_is_rejected(client):
    assert client.get("/projects/..%2Fescape").status_code in (400, 404)


# --- GET /projects/{id}/reviews/{task_id} --------------------------------


def test_a_review_is_read_back_from_the_bucket(client, s3, settings):
    """This is the path that still works once Temporal has forgotten the run."""
    project = create_project("Acme", settings)
    pdf_key = f"{project.prefix}contract-abc123.pdf"
    store_advice(s3, settings, pdf_key)
    record_review(project.id, "abc123", "legal-review-abc123", [pdf_key], settings)

    body = client.get(f"/projects/{project.id}/reviews/abc123").json()

    assert body["document_count"] == 1
    document = body["documents"][0]
    assert document["summary"] == "A services agreement."
    assert document["review_decision"] == "human_approved"
    assert document["key_risks"][0]["quote_verified"] is True
    assert document["key_risks"][0]["page"] == 5
    assert body["pending"] == []


def test_a_document_with_no_advice_yet_is_pending(client, s3, settings):
    project = create_project("Acme", settings)
    done, waiting = f"{project.prefix}a.pdf", f"{project.prefix}b.pdf"
    store_advice(s3, settings, done)
    record_review(project.id, "abc123", "legal-review-abc123", [done, waiting], settings)

    body = client.get(f"/projects/{project.id}/reviews/abc123").json()

    assert body["document_count"] == 1
    assert body["pending"] == [waiting]


def test_an_unknown_review_is_a_404(client, s3, settings):
    project = create_project("Acme", settings)

    assert client.get(f"/projects/{project.id}/reviews/nope").status_code == 404


# --- GET /projects/{id}/compare -------------------------------------------


def store_risks(s3, settings, pdf_key, risks):
    """Finished advice with whatever risks the test needs."""
    document = {
        "task_id": "abc123",
        "pdf_key": pdf_key,
        "summary": "A services agreement.",
        "key_risks": [
            {
                "description": description,
                "severity": severity,
                "location": "",
                "quote": quote,
                "page": 1,
                "quote_verified": True,
            }
            for description, severity, quote in risks
        ],
        "review_decision": ReviewDecision.AUTO_APPROVED.value,
        "question": "",
    }
    s3.objects[(settings.s3_projects, advice_key(pdf_key))] = json.dumps(document).encode()


UNCAPPED = "The Supplier's total liability under this Agreement shall be unlimited in all respects."
INDEMNITY = "The Client shall indemnify the Supplier against any and all claims arising from the Services."


@pytest.fixture
def two_rounds(s3, settings):
    """A project with round one and round two of the same contract."""
    project = create_project("Acme", settings)

    first = f"{project.prefix}services-aaaaaaaa.pdf"
    second = f"{project.prefix}services-bbbbbbbb.pdf"

    store_risks(s3, settings, first, [("liability is unlimited", "critical", UNCAPPED)])
    store_risks(s3, settings, second, [("one-sided indemnity", "high", INDEMNITY)])

    record_review(project.id, "round1", "legal-review-round1", [first], settings)
    record_review(project.id, "round2", "legal-review-round2", [second], settings, supersedes="round1")

    return project


def test_comparing_two_rounds_reports_what_moved(client, two_rounds):
    response = client.get(f"/projects/{two_rounds.id}/compare", params={"base": "round1", "against": "round2"})

    assert response.status_code == 200
    body = response.json()

    assert body["totals"]["fixed"] == 1
    assert body["totals"]["new"] == 1
    # traded a critical away for a high: better on balance
    assert body["totals"]["net_severity_change"] == -1
    assert body["document_count"] == 1


def test_the_comparison_names_both_documents(client, two_rounds):
    body = client.get(f"/projects/{two_rounds.id}/compare", params={"base": "round1", "against": "round2"}).json()
    document = body["documents"][0]

    assert document["document"].endswith("services-bbbbbbbb.pdf")
    assert document["base_document"].endswith("services-aaaaaaaa.pdf")
    assert {change["verdict"] for change in document["changes"]} == {"fixed", "new"}


def test_comparing_a_review_with_itself_shows_nothing_moved(client, two_rounds):
    body = client.get(f"/projects/{two_rounds.id}/compare", params={"base": "round1", "against": "round1"}).json()

    assert body["totals"]["unchanged"] == 1
    assert body["totals"]["net_severity_change"] == 0


def test_comparing_against_a_missing_review_is_404(client, two_rounds):
    response = client.get(f"/projects/{two_rounds.id}/compare", params={"base": "round1", "against": "nope"})

    assert response.status_code == 404


def test_comparing_inside_a_missing_project_is_404(client):
    response = client.get("/projects/no-such-project/compare", params={"base": "a", "against": "b"})

    assert response.status_code == 404


def test_a_document_with_no_advice_yet_is_reported_not_compared(client, s3, settings):
    """Round two is still running: say so rather than calling every risk fixed."""
    project = create_project("Acme", settings)
    first = f"{project.prefix}services-aaaaaaaa.pdf"
    second = f"{project.prefix}services-bbbbbbbb.pdf"

    store_risks(s3, settings, first, [("liability is unlimited", "critical", UNCAPPED)])
    record_review(project.id, "round1", "legal-review-round1", [first], settings)
    record_review(project.id, "round2", "legal-review-round2", [second], settings, supersedes="round1")

    body = client.get(f"/projects/{project.id}/compare", params={"base": "round1", "against": "round2"}).json()

    assert body["documents"] == []
    assert body["not_reviewed_yet"] == [second]


def test_a_review_records_what_it_supersedes(client, settings):
    project = create_project("Acme", settings)
    record_review(project.id, "round1", "legal-review-round1", ["a.pdf"], settings)
    record_review(project.id, "round2", "legal-review-round2", ["b.pdf"], settings, supersedes="round1")

    reviews = {review["task_id"]: review for review in client.get(f"/projects/{project.id}").json()["reviews"]}

    assert reviews["round2"]["supersedes"] == "round1"
    assert reviews["round1"]["supersedes"] == ""
