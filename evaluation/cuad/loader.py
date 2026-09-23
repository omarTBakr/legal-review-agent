"""Reading CUAD's SQuAD-shaped JSON into something the scorers can use.

CUAD stores one "paragraph" per contract holding its whole plain text, and 41
questions against it, one per clause category, each with zero or more annotated
answer spans. The category is the part of the question id after the double
underscore; the prose before "Details:" in the question is boilerplate, and the
part after it is the definition worth putting in an extraction prompt.

A category with no answers is a genuine negative — the annotators looked and
found nothing — which is what makes an abstention scoreable.
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CATEGORIES_FILE = Path(__file__).parent / "risk_categories.json"

_ID_SEPARATOR = "__"
_DETAILS = "Details:"


@dataclass(frozen=True)
class Clause:
    """One category's annotation for one contract."""

    category: str
    definition: str
    spans: tuple[str, ...]

    @property
    def annotated(self) -> bool:
        """Whether the annotators found this category in the contract."""
        return bool(self.spans)


@dataclass(frozen=True)
class Contract:
    """One CUAD contract: its text and every category's annotation."""

    title: str
    text: str
    clauses: tuple[Clause, ...] = field(default_factory=tuple)

    def clause(self, category: str) -> Clause | None:
        return next((clause for clause in self.clauses if clause.category == category), None)

    def annotated_clauses(self, categories: set[str] | None = None) -> list[Clause]:
        """The clauses actually present, optionally narrowed to some categories."""
        return [clause for clause in self.clauses if clause.annotated and (categories is None or clause.category in categories)]

    @property
    def spans_total(self) -> int:
        return sum(len(clause.spans) for clause in self.clauses)


def _split_question(question: str) -> str:
    """The definition out of a CUAD question, or the whole question if it has none."""
    head, _, tail = question.partition(_DETAILS)

    return (tail or head).strip()


def parse_contracts(payload: dict) -> list[Contract]:
    """Turns the loaded JSON into contracts, in the order CUAD lists them."""
    contracts = []

    for entry in payload.get("data") or []:
        clauses = []
        text = ""

        for paragraph in entry.get("paragraphs") or []:
            # one paragraph per contract in CUAD v1; concatenating is defensive
            # only in the sense that it keeps the text complete if that changes
            text += paragraph.get("context") or ""

            for question in paragraph.get("qas") or []:
                category = str(question.get("id", "")).split(_ID_SEPARATOR)[-1].strip()
                if not category:
                    continue
                clauses.append(
                    Clause(
                        category=category,
                        definition=_split_question(str(question.get("question", ""))),
                        spans=tuple(
                            str(answer.get("text", "")).strip()
                            for answer in question.get("answers") or []
                            if str(answer.get("text", "")).strip()
                        ),
                    )
                )

        contracts.append(Contract(title=str(entry.get("title", "")), text=text, clauses=tuple(clauses)))

    return contracts


def load_contracts(path: Path) -> list[Contract]:
    """Reads and parses CUADv1.json."""
    return parse_contracts(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class RiskCategories:
    """The curated view of CUAD's categories: which are risks, which are high-stakes."""

    risky: frozenset[str]
    high_stakes: frozenset[str]
    names: dict[str, tuple[str, ...]]
    notes: dict[str, str]

    @property
    def all_categories(self) -> frozenset[str]:
        return frozenset(self.notes)

    def is_high_stakes(self, category: str) -> bool:
        return category in self.high_stakes


@lru_cache(maxsize=1)
def load_risk_categories(path: Path = CATEGORIES_FILE) -> RiskCategories:
    """The curated mapping, read once."""
    payload = json.loads(path.read_text(encoding="utf-8"))["categories"]

    return RiskCategories(
        risky=frozenset(name for name, entry in payload.items() if entry.get("risky")),
        high_stakes=frozenset(name for name, entry in payload.items() if entry.get("high_stakes")),
        names={name: tuple(entry.get("names") or ()) for name, entry in payload.items()},
        notes={name: str(entry.get("note", "")) for name, entry in payload.items()},
    )
