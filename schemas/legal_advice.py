from dataclasses import dataclass, field

from enums.ReviewDecision import ReviewDecision
from schemas.key_risk import KeyRisk
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LegalAdvice:
    """
    What the model produced for one document.

    `s3_path` is empty until the advice has been stored; the workflow fills it
    in on the way out.
    """

    summary: str
    key_risks: list[KeyRisk] = field(default_factory=list)
    needs_human: bool = False
    question: str = ""
    review_decision: ReviewDecision = ReviewDecision.AUTO_APPROVED
    s3_path: str = ""
    # risks the model returned that could not be read, and were dropped. Kept as
    # a number rather than discarded silently: "we found three risks" and "we
    # found three and lost one" are different claims about a document
    malformed_risks: int = 0

    @classmethod
    def from_model(cls, raw: dict) -> "LegalAdvice":
        """
        Validates a model reply into advice.

        This is the boundary where the LLM stops being trusted. A reply whose
        *shape* is wrong — not an object, no summary, key_risks not a list —
        raises ValueError, which the activity turns into an LLMResponseError and
        retries: the model did not follow the format and asking again is the
        right move.

        **One malformed risk inside an otherwise good reply is dropped, not
        fatal.** A smaller model will occasionally return a risk with an empty
        severity, and failing the whole document over it throws away every other
        risk in the contract — a 25-contract sweep on gemma4 lost a complete
        review that way. Retrying does not reliably help either, because the
        reply is not deterministic. The count is kept so the loss is visible.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"advice must be an object, got {type(raw).__name__}")

        summary = str(raw.get("summary", "")).strip()
        if not summary:
            raise ValueError("advice needs a summary")

        risks = raw.get("key_risks") or []
        if not isinstance(risks, list):
            raise ValueError("key_risks must be a list")

        needs_human = bool(raw.get("needs_human", False))
        question = str(raw.get("question", "")).strip()
        if needs_human and not question:
            raise ValueError("needs_human is set but no question was asked")

        parsed, malformed = [], 0
        for risk in risks:
            try:
                parsed.append(KeyRisk.from_model(risk))
            except ValueError as exc:
                # named in the log, because a risk being dropped is a fact about
                # the review and somebody may have to go and look at the clause
                logger.warning("dropping a risk that could not be read (%s): %s", exc, str(risk)[:160])
                malformed += 1

        return cls(
            summary=summary,
            key_risks=parsed,
            needs_human=needs_human,
            question=question,
            malformed_risks=malformed,
        )

    @property
    def worst_severity(self):
        """The severity of the most serious risk, or None when there are none."""
        return max((risk.severity for risk in self.key_risks), default=None)
