from dataclasses import dataclass

from enums.RiskSeverity import RiskSeverity
from enums.VerificationStatus import VerificationStatus


@dataclass
class KeyRisk:
    """
    One thing in a document that a lawyer would want to look at.

    `quote` is the passage the risk rests on, as the model copied it, and
    `page` is where it is. `quote_verified` is only ever set by the evidence
    check in utils/evidence.py, never by the model: it says the quote was
    actually found in the document.
    """

    description: str
    severity: RiskSeverity
    location: str = ""
    quote: str = ""
    page: int | None = None
    confidence: float = 0.0
    category: str = ""
    recommended_action: str = ""
    quote_verified: bool = False

    @classmethod
    def from_model(cls, raw: dict) -> "KeyRisk":
        """
        Builds a risk from whatever the model returned.

        Raises ValueError on anything missing or unrecognised, so a bad reply
        fails the activity instead of quietly producing a risk with no severity.
        Every finding must contain its evidence and review metadata. The
        evidence check may correct the page after locating the quote, but a
        missing model page is still a malformed finding.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"a risk must be an object, got {type(raw).__name__}")

        description = str(raw.get("description", "")).strip()
        if not description:
            raise ValueError("a risk needs a description")

        quote = str(raw.get("quote") or "").strip()
        if not quote:
            raise ValueError("a risk needs an exact quote")

        page = _as_page(raw.get("page"))
        if page is None:
            raise ValueError("a risk needs a positive page number")

        confidence = _as_confidence(raw.get("confidence"))
        category = str(raw.get("category", "")).strip()
        if not category:
            raise ValueError("a risk needs a category")

        recommended_action = str(raw.get("recommended_action", "")).strip()
        if not recommended_action:
            raise ValueError("a risk needs a recommended action")

        return cls(
            description=description,
            severity=RiskSeverity.parse(raw.get("severity", "")),
            location=str(raw.get("location", "")).strip(),
            quote=quote,
            page=page,
            confidence=confidence,
            category=category,
            recommended_action=recommended_action,
        )

    def to_prompt_dict(self) -> dict:
        """The risk as a prompt shows it to the model: everything but the verification flag."""
        return {
            "description": self.description,
            "severity": self.severity.value,
            "location": self.location,
            "quote": self.quote,
            "page": self.page,
            "confidence": self.confidence,
            "category": self.category,
            "recommended_action": self.recommended_action,
        }

    def to_dict(self) -> dict:
        """The risk as the API and the stored advice JSON report it."""
        return {
            **self.to_prompt_dict(),
            "quote_verified": self.quote_verified,
            "verification_status": self.verification_status.value,
        }

    @property
    def verification_status(self) -> VerificationStatus:
        """The evidence-derived status exposed by API and stored advice."""
        return VerificationStatus.VERIFIED if self.quote_verified else VerificationStatus.UNVERIFIED


def _as_page(value) -> int | None:
    """A positive page number, or None for anything else the model might send."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


def _as_confidence(value) -> float:
    """A model confidence score bounded to the inclusive 0..1 interval."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise ValueError("a risk needs a numeric confidence score") from None

    if not 0.0 <= confidence <= 1.0:
        raise ValueError("risk confidence must be between 0 and 1")

    return confidence
