"""
Every risk in a project, in one list.

Each review is an island: to answer "what are the worst things across this
client's twelve contracts" you have to open twelve pages and hold the answer in
your head. This reads them all and sorts them, worst first.

Nothing new is computed and no model is called — the advice is already in the
bucket and this is a different way through it. What it costs is object reads,
one per document, so they are issued **concurrently** rather than one after the
other, the same reason `utils/review_context.py` does.

Superseded rounds are dropped by default. A project with four rounds of one
contract would otherwise report the same liability cap four times and rank the
document by how often it was negotiated rather than by how bad it is.
"""

import asyncio
from dataclasses import dataclass, field

from enums.RiskSeverity import RiskSeverity
from exceptions.storage import ObjectNotFoundError
from schemas.project import ProjectReview
from utils.advice_store import read_advice
from utils.config import Settings
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RegisterEntry:
    """One risk, with enough about where it came from to open it."""

    task_id: str
    pdf_key: str
    description: str
    severity: str
    location: str = ""
    quote: str = ""
    page: int | None = None
    quote_verified: bool = False
    submitted_at: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "pdf_key": self.pdf_key,
            "description": self.description,
            "severity": self.severity,
            "location": self.location,
            "quote": self.quote,
            "page": self.page,
            "quote_verified": self.quote_verified,
            "submitted_at": self.submitted_at,
        }


@dataclass
class Register:
    """A project's risks, and what they add up to."""

    project_id: str
    entries: list[RegisterEntry] = field(default_factory=list)
    documents: int = 0
    # reviews that are still running, or whose advice was never stored
    pending: list[str] = field(default_factory=list)
    superseded_reviews: int = 0

    @property
    def counts(self) -> dict[str, int]:
        """How many of each severity, every band present so a UI can rely on it."""
        counts = {severity.value: 0 for severity in RiskSeverity}

        for entry in self.entries:
            counts[entry.severity] += 1

        return counts

    @property
    def unverified(self) -> int:
        """Risks resting on a quote the evidence check could not find."""
        return sum(1 for entry in self.entries if not entry.quote_verified)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "risks": [entry.to_dict() for entry in self.entries],
            "risk_count": len(self.entries),
            "documents": self.documents,
            "counts": self.counts,
            "unverified": self.unverified,
            "pending": list(self.pending),
            "superseded_reviews": self.superseded_reviews,
        }


def current_reviews(reviews: list[ProjectReview]) -> list[ProjectReview]:
    """
    The reviews that are nobody's predecessor: the latest round of each contract.

    A review is superseded when another names it in `supersedes`. Reviews from
    before rounds existed name nothing and supersede nothing, so they are all
    current, which is the right answer for them.
    """
    superseded = {review.supersedes for review in reviews if review.supersedes}

    return [review for review in reviews if review.task_id not in superseded]


def sort_entries(entries: list[RegisterEntry]) -> None:
    """
    Worst first, then newest, then by document, in place.

    Three stable sorts applied least-significant first, rather than one key
    function: severity wants descending and so does the date, and a string
    cannot be negated inside a tuple the way a number can.
    """
    entries.sort(key=lambda entry: entry.pdf_key)
    entries.sort(key=lambda entry: entry.submitted_at, reverse=True)
    entries.sort(key=lambda entry: RiskSeverity.parse(entry.severity).rank, reverse=True)


async def build_register(
    project_id: str,
    reviews: list[ProjectReview],
    settings: Settings,
    include_superseded: bool = False,
    minimum: RiskSeverity | None = None,
) -> Register:
    """
    Reads every document's advice in the project and lays the risks out flat.

    `minimum` drops anything below a severity — the usual ask is "just show me
    the criticals" — and is applied after reading, so the counts a caller sees
    are of what it asked for and nothing is silently missing from them.
    """
    chosen = reviews if include_superseded else current_reviews(reviews)
    register = Register(project_id=project_id, superseded_reviews=len(reviews) - len(chosen))

    wanted = [(review, pdf_key) for review in chosen for pdf_key in review.pdf_keys]
    results = await asyncio.gather(*(asyncio.to_thread(_read_one, pdf_key, settings) for _, pdf_key in wanted))

    for (review, pdf_key), advice in zip(wanted, results, strict=True):
        if advice is None:
            register.pending.append(pdf_key)
            continue

        register.documents += 1

        for risk in advice.key_risks:
            if minimum is not None and risk.severity < minimum:
                continue
            register.entries.append(
                RegisterEntry(
                    task_id=review.task_id,
                    pdf_key=pdf_key,
                    description=risk.description,
                    severity=risk.severity.value,
                    location=risk.location,
                    quote=risk.quote,
                    page=risk.page,
                    quote_verified=risk.quote_verified,
                    submitted_at=review.submitted_at,
                )
            )

    sort_entries(register.entries)

    logger.info(
        "register for %s: %d risk(s) across %d document(s), %d pending",
        project_id,
        len(register.entries),
        register.documents,
        len(register.pending),
    )

    return register


def _read_one(pdf_key: str, settings: Settings):
    """One document's advice, or None when it has none yet. Runs in a thread."""
    try:
        return read_advice(pdf_key, settings, settings.s3_projects)
    except ObjectNotFoundError:
        return None
    except ValueError:
        # stored advice that will not parse should not take the register with it
        logger.warning("advice for %s is unreadable; leaving it out of the register", pdf_key)
        return None
