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
                quote_verified=verified,
            )
        ],
        review_decision=ReviewDecision.HUMAN_APPROVED,
    )
    document = {
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
