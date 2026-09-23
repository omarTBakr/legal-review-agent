"""
The shared-key gate.

Two things worth testing and easy to get wrong: that a route added later is
covered without anyone remembering to cover it, and that an empty API_KEY means
open rather than "nothing can ever authenticate". The first is why the
dependency hangs off the routers in main.py, and the test below walks the app's
real routes rather than a list written by hand.
"""

import pytest
from fastapi.testclient import TestClient

import utils.config
from main import app
from utils.auth import HEADER_NAME, is_open, key_matches, require_api_key, warn_if_open

KEY = "s3cret-key-value"

# routes that are open on purpose, and why
OPEN_PATHS = {
    "/health": "a monitor should not need the secret to see the process is alive",
    "/": "redirects to the UI, which has to load before it can ask for a key",
    "/ui": "the page itself",
    "/openapi.json": "FastAPI's own schema",
    "/docs": "FastAPI's own docs",
    "/docs/oauth2-redirect": "FastAPI's own docs",
    "/redoc": "FastAPI's own docs",
}


@pytest.fixture
def keyed(monkeypatch):
    """A configured key, with the settings singleton reset around it."""
    monkeypatch.setenv("API_KEY", KEY)
    utils.config._settings_instance = None
    yield KEY
    utils.config._settings_instance = None


def test_no_key_configured_leaves_the_api_open():
    """A laptop run must not need a secret it was never given."""
    assert is_open() is True


def test_a_configured_key_closes_it(keyed):
    assert is_open() is False


def test_warn_if_open_says_so_only_when_it_is(keyed, monkeypatch):
    assert warn_if_open() is False

    monkeypatch.delenv("API_KEY")
    utils.config._settings_instance = None

    assert warn_if_open() is True


def test_the_warning_names_the_setting(caplog):
    """Whoever reads the log should know which variable to set."""
    with caplog.at_level("WARNING"):
        warn_if_open()

    assert "API_KEY" in caplog.text


@pytest.mark.parametrize(
    ("offered", "expected", "matches"),
    [
        (KEY, KEY, True),
        ("", KEY, False),
        (KEY[:-1], KEY, False),
        (KEY + "x", KEY, False),
        (KEY.upper(), KEY, False),
        ("", "", True),
    ],
)
def test_key_matches(offered, expected, matches):
    assert key_matches(offered, expected) is matches


async def test_the_dependency_passes_when_open():
    """Nothing raises, and nothing is required."""
    assert await require_api_key("") is None


async def test_the_dependency_rejects_a_wrong_key(keyed):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        await require_api_key("not-the-key")

    assert caught.value.status_code == 401
    # the message must not say what was wrong with it; "too short" is a hint
    assert "too short" not in caught.value.detail.lower()
    assert HEADER_NAME in caught.value.detail


async def test_the_dependency_accepts_the_right_key(keyed):
    assert await require_api_key(KEY) is None


def guarded_operations() -> list[tuple[str, str]]:
    """
    Every (method, path) the app documents, minus the deliberately open ones.

    Read off the OpenAPI schema rather than `app.routes`: this FastAPI version
    wraps an included router in an object with no `.path`, and the schema is the
    same list the UI's own endpoint test checks against.
    """
    operations = []

    for path, methods in app.openapi()["paths"].items():
        if path in OPEN_PATHS:
            continue
        operations += [(method.upper(), path) for method in methods]

    return sorted(operations)


def test_there_are_guarded_routes_to_check():
    """An empty list would make the test below pass by doing nothing."""
    assert len(guarded_operations()) > 5


def test_every_route_but_the_open_ones_requires_the_key(keyed):
    """
    Walks the app's own schema, so an endpoint added later is checked by having
    been added — which is the whole reason the dependency is on the routers
    rather than on each route.
    """
    client = TestClient(app)
    answered_without_a_key = []

    for method, path in guarded_operations():
        # a placeholder for every path parameter: the key is checked before the
        # handler runs, so it never has to be a real id
        concrete = path
        while "{" in concrete:
            before, rest = concrete.split("{", 1)
            _, after = rest.split("}", 1)
            concrete = f"{before}x{after}"

        response = client.request(method, concrete)

        if response.status_code != 401:
            answered_without_a_key.append(f"{method} {path} -> {response.status_code}")

    assert not answered_without_a_key, "these answered without a key:\n  " + "\n  ".join(answered_without_a_key)


def test_health_stays_open(keyed):
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_a_guarded_route_answers_with_the_key(keyed):
    """
    The key gets past the gate. /projects then fails on the bucket, which is a
    different failure and the point: it got as far as the handler.
    """
    response = TestClient(app).get("/projects", headers={HEADER_NAME: KEY})

    assert response.status_code != 401
