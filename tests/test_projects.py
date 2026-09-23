"""The project store, against the in-memory S3 fake. A project is a prefix in
the bucket, so these mostly check what lands under which key."""

import json

import pytest

from exceptions.storage import ObjectNotFoundError
from exceptions.validation import ValidationError
from utils.projects import (
    create_project,
    get_project,
    list_projects,
    list_reviews,
    manifest_key,
    record_review,
    slugify,
    validate_email,
)

# --- slugify -------------------------------------------------------------


@pytest.mark.parametrize(
    "name, slug",
    [
        ("Acme NDAs", "acme-ndas"),
        ("  Spaces   everywhere  ", "spaces-everywhere"),
        ("Café Ltd", "cafe-ltd"),
        ("2026 / Q1 — leases", "2026-q1-leases"),
        ("!!!", "project"),
        ("", "project"),
    ],
)
def test_slugify(name, slug):
    assert slugify(name) == slug


def test_a_long_name_is_cut_and_does_not_end_in_a_hyphen():
    slug = slugify("a" * 39 + " and then some more words")

    assert len(slug) <= 40
    assert not slug.endswith("-")


# --- create_project ------------------------------------------------------


def test_creating_a_project_writes_its_manifest(s3, settings):
    project = create_project("Acme NDAs", settings, description="Standard NDAs", email="legal@acme.test")

    stored = json.loads(s3.objects[(settings.s3_projects, manifest_key(project.id))])
    assert stored["name"] == "Acme NDAs"
    assert stored["description"] == "Standard NDAs"
    assert stored["email"] == "legal@acme.test"
    assert stored["prefix"] == f"{project.id}/"


def test_the_id_is_the_slug_plus_a_suffix(s3, settings):
    project = create_project("Acme NDAs", settings)

    assert project.id.startswith("acme-ndas-")
    assert len(project.id) == len("acme-ndas-") + 8


def test_two_projects_with_one_name_get_their_own_folders(s3, settings):
    first = create_project("NDAs", settings)
    second = create_project("NDAs", settings)

    assert first.id != second.id
    assert first.prefix != second.prefix


def test_a_project_needs_a_name(s3, settings):
    with pytest.raises(ValidationError, match="needs a name"):
        create_project("   ", settings)


def test_the_description_and_email_are_optional(s3, settings):
    project = create_project("NDAs", settings)

    assert project.description == "" and project.email == ""


def test_a_nonsense_email_is_rejected(s3, settings):
    with pytest.raises(ValidationError, match="not an email"):
        create_project("NDAs", settings, email="not-an-address")


@pytest.mark.parametrize("email", ["legal@acme.test", " legal@acme.test "])
def test_a_real_email_is_kept_and_trimmed(email):
    assert validate_email(email) == "legal@acme.test"


# --- get_project / list_projects -----------------------------------------


def test_a_project_reads_back(s3, settings):
    created = create_project("Acme NDAs", settings, email="legal@acme.test")

    assert get_project(created.id, settings) == created


def test_an_unknown_project_is_not_found(s3, settings):
    with pytest.raises(ObjectNotFoundError):
        get_project("nothing-here", settings)


@pytest.mark.parametrize("project_id", ["../escape", "with/slash", "UPPER", "", "-leading"])
def test_an_id_that_could_escape_the_prefix_is_rejected(s3, settings, project_id):
    """The id is pasted into an S3 key, so it is checked rather than escaped."""
    with pytest.raises(ValidationError):
        get_project(project_id, settings)


def test_projects_are_listed_newest_first(s3, settings, monkeypatch):
    import utils.projects

    stamps = iter(["2026-01-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00"])
    monkeypatch.setattr(utils.projects, "_now", lambda: next(stamps))

    old = create_project("Old", settings)
    new = create_project("New", settings)

    assert [project.id for project in list_projects(settings)] == [new.id, old.id]


def test_listing_ignores_the_documents_in_a_project(s3, settings):
    project = create_project("Acme", settings)
    s3.objects[(settings.s3_projects, f"{project.prefix}contract-a1b2c3d4.pdf")] = b"%PDF-"

    assert [found.id for found in list_projects(settings)] == [project.id]


def test_an_unreadable_manifest_does_not_hide_the_others(s3, settings):
    project = create_project("Acme", settings)
    s3.objects[(settings.s3_projects, "broken/project.json")] = b"{not json"

    assert [found.id for found in list_projects(settings)] == [project.id]


# --- reviews -------------------------------------------------------------


def test_a_review_is_recorded_under_the_project(s3, settings):
    project = create_project("Acme", settings)

    record_review(project.id, "abc123", "legal-review-abc123", ["a.pdf", "b.pdf"], settings)

    [review] = list_reviews(project.id, settings)
    assert review.task_id == "abc123"
    assert review.workflow_id == "legal-review-abc123"
    assert review.pdf_keys == ["a.pdf", "b.pdf"]
    assert review.submitted_at


def test_each_review_is_its_own_object_so_two_cannot_collide(s3, settings):
    """A single manifest listing every review would lose one of these."""
    project = create_project("Acme", settings)

    record_review(project.id, "aaa", "legal-review-aaa", ["a.pdf"], settings)
    record_review(project.id, "bbb", "legal-review-bbb", ["b.pdf"], settings)

    assert {review.task_id for review in list_reviews(project.id, settings)} == {"aaa", "bbb"}


def test_a_project_with_no_reviews_lists_none(s3, settings):
    project = create_project("Acme", settings)

    assert list_reviews(project.id, settings) == []
