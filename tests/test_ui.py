"""The browser UI is static files served by the API. These check that it is
wired up and speaks to the real routes, not how it behaves in a browser."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
MODULES = sorted(path.relative_to(UI_DIR).as_posix() for path in UI_DIR.rglob("*.js"))


@pytest.fixture
def client():
    return TestClient(app)


def test_the_root_redirects_to_the_ui(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_the_ui_without_a_trailing_slash_redirects(client):
    response = client.get("/ui", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/ui/")


def test_the_page_is_served(client):
    response = client.get("/ui/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Legal Review Agent</title>" in response.text


def test_the_entry_point_is_a_module(client):
    """The UI is ES modules with no build step, so the browser has to be told."""
    page = client.get("/ui/").text

    assert '<script type="module" src="/ui/js/main.js"></script>' in page
    assert 'href="/ui/styles.css"' in page


@pytest.mark.parametrize("module", MODULES)
def test_every_module_is_served(client, module):
    response = client.get(f"/ui/{module}")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_the_ui_is_revalidated_rather_than_cached(client):
    """A stale module behind a fresh index.html runs half the old code."""
    for path in ("/ui/", "/ui/js/main.js", "/ui/styles.css"):
        assert client.get(path).headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("module", MODULES)
def test_every_import_resolves_to_a_served_file(client, module):
    """A typo in an import path is a blank page, and nothing else would catch it."""
    source = (UI_DIR / module).read_text()
    here = (UI_DIR / module).parent

    for target in re.findall(r'^\s*import\s[^"\']*["\'](\.[^"\']+)["\']', source, re.MULTILINE):
        resolved = (here / target).resolve().relative_to(UI_DIR)
        assert client.get(f"/ui/{resolved.as_posix()}").status_code == 200, f"{module} imports {target}"


def test_every_endpoint_the_ui_calls_exists(client):
    """Guards against the page drifting from the routes it calls."""
    source = (UI_DIR / "js/api.js").read_text()
    paths = app.openapi()["paths"]

    # the endpoints table holds either a literal path or a template built from
    # one, so both spellings reduce to what OpenAPI calls the path
    called = set()
    for line in source.splitlines():
        match = re.search(r'["\'`](/[a-z]+[^"\'`]*)["\'`]', line)
        if match and "endpoints" not in match.group(1):
            called.add(re.sub(r"\$\{[^}]+\}", "{id}", match.group(1)))

    expected = {
        "/health",
        "/legal",
        "/legal/{id}",
        "/legal/{id}/respond",
        "/projects",
        "/projects/{id}",
        "/projects/{id}/reviews/{id}",
        "/projects/{id}/reviews/{id}/chat",
        "/projects/{id}/reviews/{id}/audio/{id}/{id}",
        "/voice/transcribe",
        "/voice/speak",
    }
    assert expected <= called

    for path in (
        "/health",
        "/legal",
        "/legal/{task_id}",
        "/legal/{task_id}/respond",
        "/projects",
        "/projects/{project_id}",
        "/projects/{project_id}/reviews/{task_id}",
        "/projects/{project_id}/reviews/{task_id}/chat",
        "/projects/{project_id}/reviews/{task_id}/audio/{turn}/{kind}",
        "/voice/transcribe",
        "/voice/speak",
    ):
        assert path in paths


def strip_comments(source: str) -> str:
    """Code only: a comment is allowed to discuss what the code must not do."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)

    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


@pytest.mark.parametrize("module", MODULES)
def test_no_module_uses_inner_html(client, module):
    """Model output is rendered as text; innerHTML would let it inject markup."""
    assert "innerHTML" not in strip_comments((UI_DIR / module).read_text())


def test_the_ui_stays_out_of_the_api_schema(client):
    paths = app.openapi()["paths"]

    assert "/" not in paths
    assert not any(path.startswith("/ui") for path in paths)
