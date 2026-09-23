from dataclasses import dataclass

from enums.RiskSeverity import RiskSeverity


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
    quote_verified: bool = False

    @classmethod
    def from_model(cls, raw: dict) -> "KeyRisk":
        """
        Builds a risk from whatever the model returned.

        Raises ValueError on anything missing or unrecognised, so a bad reply
        fails the activity instead of quietly producing a risk with no severity.
        A missing or nonsensical page is not an error: the evidence check
        fills in the real one when it finds the quote.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"a risk must be an object, got {type(raw).__name__}")

        description = str(raw.get("description", "")).strip()
        if not description:
            raise ValueError("a risk needs a description")

        return cls(
            description=description,
            severity=RiskSeverity.parse(raw.get("severity", "")),
            location=str(raw.get("location", "")).strip(),
            quote=str(raw.get("quote") or "").strip(),
            page=_as_page(raw.get("page")),
        )

    def to_prompt_dict(self) -> dict:
        """The risk as a prompt shows it to the model: everything but the verification flag."""
        return {
            "description": self.description,
            "severity": self.severity.value,
            "location": self.location,
            "quote": self.quote,
            "page": self.page,
        }

    def to_dict(self) -> dict:
        """The risk as the API and the stored advice JSON report it."""
        return {**self.to_prompt_dict(), "quote_verified": self.quote_verified}


def _as_page(value) -> int | None:
    """A positive page number, or None for anything else the model might send."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None
