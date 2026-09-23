"""Fixtures for the evaluation's own tests.

Mirrors tests/conftest.py in the one thing that matters here — no real settings,
no real credentials — without pulling in the S3 and voice fakes the pipeline
tests need and these do not.
"""

import pytest

import evaluation.config
import utils.config
from enums.RiskSeverity import RiskSeverity
from evaluation.cuad.loader import Clause, Contract, RiskCategories
from schemas.key_risk import KeyRisk


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    """Fake credentials and a throwaway scratch directory, as the main suite does."""
    env = {
        "AWS_ACCESS_KEY_ID": "test-key",
        "AWS_SECRET_ACCESS_KEY": "test-secret",
        "AWS_REGION": "us-east-1",
        "AWS_ENDPOINT_URL": "https://s3.example.com",
        "S3_PDF_BUCKET": "test-pdfs",
        "S3_PARSED_MDS": "test-mds",
        "TEMP_PD_DIR": str(tmp_path),
        "TEMP_PDF_FOLDER": "TEMP_PDF",
        "TEMP_MD_FOLDER": "TEMP_MD",
        # pinned, not inherited: without it the suite reads whatever provider
        # the developer's .env happens to name, and passes or fails accordingly
        "LLM_PROVIDER": "openrouter",
        "OPENROUTER_API_KEY": "test-llm-key",
        "OPENROUTER_MODEL": "test/reviewer",
        "OLLAMA_MODEL": "test/local",
        "EVAL_JUDGE_MODEL": "test/judge",
        "LEGAL_PAGES_PER_BATCH": "2",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(utils.config, "_settings_instance", None)
    monkeypatch.setattr(evaluation.config, "_instance", None)

    return utils.config.get_setting()


def risk(description: str, severity: str = "high", quote: str = "", **extra) -> KeyRisk:
    """A KeyRisk without the ceremony, for tests that only care about one field."""
    return KeyRisk(
        description=description,
        severity=RiskSeverity.parse(severity),
        location=extra.get("location", ""),
        quote=quote,
        page=extra.get("page"),
        quote_verified=extra.get("quote_verified", False),
    )


@pytest.fixture
def make_risk():
    return risk


@pytest.fixture
def categories():
    """A two-category curated map: one risky and high-stakes, one not risky."""
    return RiskCategories(
        risky=frozenset({"Uncapped Liability", "Non-Compete"}),
        high_stakes=frozenset({"Uncapped Liability"}),
        names={"Uncapped Liability": ("unlimited liability",), "Non-Compete": ("non-compete",), "Parties": ("parties",)},
        notes={"Uncapped Liability": "", "Non-Compete": "", "Parties": ""},
    )


@pytest.fixture
def contract():
    """One contract with one annotated risky span and one annotated metadata span."""
    return Contract(
        title="ACME-SUPPLY-AGREEMENT",
        text="1. Liability\n\nThe Buyer's liability under this Agreement is unlimited.\n\n2. Parties\n\nAcme Ltd and Buyer Ltd.",
        clauses=(
            Clause(
                category="Uncapped Liability",
                definition="Is there a clause with no liability cap?",
                spans=("The Buyer's liability under this Agreement is unlimited.",),
            ),
            Clause(category="Non-Compete", definition="Is there a non-compete?", spans=()),
            Clause(category="Parties", definition="Who signed?", spans=("Acme Ltd and Buyer Ltd.",)),
        ),
    )
