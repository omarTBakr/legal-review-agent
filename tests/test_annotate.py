"""
Marking up the original PDF.

Built on real PDFs made here rather than fixtures, because the thing being
tested is whether pymupdf can find a quote in a page — which depends on how the
text was laid out, and a hand-written fixture would not have the wrapping that
makes it hard.
"""

import json

import pymupdf
import pytest
from fastapi.testclient import TestClient

from enums.ReviewDecision import ReviewDecision
from enums.RiskSeverity import RiskSeverity
from main import app
from schemas.key_risk import KeyRisk
from utils.advice_store import advice_key
from utils.annotate import _chunks, annotate, find_quote
from utils.projects import create_project

SHORT = "The Supplier's liability is unlimited."
LONG = (
    "The Client's aggregate liability under this Agreement is unlimited and is not capped by "
    "reference to the fees paid, while the Supplier's total aggregate liability for all claims "
    "arising under or in connection with this Agreement is limited to ten per cent of the fees."
)


def make_pdf(*paragraphs: str, width: float = 400) -> bytes:
    """A PDF whose text wraps, so a long quote really does span several lines."""
    document = pymupdf.open()
    page = document.new_page()
    box = pymupdf.Rect(50, 50, 50 + width, 750)

    page.insert_textbox(box, "\n\n".join(paragraphs), fontsize=11, fontname="helv")
    data = document.tobytes()
    document.close()

    return data


def risk(description: str, quote: str, severity: str = "high", page: int | None = 1, verified: bool = True) -> KeyRisk:
    return KeyRisk(
        description=description,
        severity=RiskSeverity.parse(severity),
        location="clause 6.1",
        quote=quote,
        page=page,
        quote_verified=verified,
    )


def annotations(pdf: bytes) -> list[dict]:
    """Every annotation in a PDF, as {page, content}."""
    document = pymupdf.open(stream=pdf, filetype="pdf")
    found = [
        {"page": index + 1, "content": annotation.info.get("content", ""), "type": annotation.type[1]}
        for index, page in enumerate(document)
        for annotation in page.annots()
    ]
    document.close()

    return found


class TestChunks:
    def test_a_short_quote_is_searched_whole(self):
        assert _chunks(SHORT) == [SHORT]

    def test_a_long_quote_gets_overlapping_fallbacks(self):
        pieces = _chunks(LONG)

        assert pieces[0] == LONG
        assert len(pieces) > 1
        # overlapping, so a piece that straddles a line break leaves no hole
        assert pieces[1].split()[6:] == pieces[2].split()[:6]


class TestFindQuote:
    def test_a_quote_on_one_line_is_found(self):
        document = pymupdf.open(stream=make_pdf(SHORT), filetype="pdf")

        page, rectangles = find_quote(document, SHORT, 1)

        assert page == 1
        assert rectangles
        document.close()

    def test_a_quote_that_wraps_is_still_found(self):
        """The case the chunking exists for: search_for cannot match it whole."""
        document = pymupdf.open(stream=make_pdf(LONG), filetype="pdf")

        page, rectangles = find_quote(document, LONG, 1)

        assert page == 1
        # several boxes, one per line it covers
        assert len(rectangles) > 1
        document.close()

    def test_a_quote_that_is_not_there_is_not_found(self):
        document = pymupdf.open(stream=make_pdf(SHORT), filetype="pdf")

        page, rectangles = find_quote(document, "The parties agree to arbitrate in Singapore.", 1)

        assert (page, rectangles) == (0, [])
        document.close()

    def test_a_wrong_page_hint_does_not_lose_the_quote(self):
        """The recorded page can be wrong; the highlight belongs where the text is."""
        document = pymupdf.open(stream=make_pdf(SHORT), filetype="pdf")

        page, rectangles = find_quote(document, SHORT, page_hint=9)

        assert page == 1
        assert rectangles
        document.close()


class TestAnnotate:
    def test_a_verified_quote_becomes_a_highlight(self):
        marked, report = annotate(make_pdf(SHORT), [risk("liability is unlimited", SHORT)])

        assert report == {"risks": 1, "highlighted": 1, "listed_only": 0}
        found = annotations(marked)
        assert len(found) == 1
        assert found[0]["type"] == "Highlight"
        assert "liability is unlimited" in found[0]["content"]

    def test_the_note_carries_the_severity_and_the_clause(self):
        marked, _ = annotate(make_pdf(SHORT), [risk("liability is unlimited", SHORT, "critical")])

        content = annotations(marked)[0]["content"]

        assert "CRITICAL" in content
        assert "clause 6.1" in content

    def test_a_suggestion_is_included_when_there_is_one(self):
        """Ready for suggested redlines; harmless until KeyRisk has the field."""
        one = risk("liability is unlimited", SHORT)
        one.suggestion = "Cap the Client's liability at the fees paid in the preceding 12 months."

        content = annotations(annotate(make_pdf(SHORT), [one])[0])[0]["content"]

        assert "Suggested wording" in content
        assert "preceding 12 months" in content

    def test_an_unverified_quote_is_listed_not_highlighted(self):
        marked, report = annotate(make_pdf(SHORT), [risk("unverified thing", SHORT, verified=False)])

        assert report == {"risks": 1, "highlighted": 0, "listed_only": 1}
        assert annotations(marked) == []

        document = pymupdf.open(stream=marked, filetype="pdf")
        # nothing is dropped: it is on the appendix page
        assert "could not be located" in document[-1].get_text()
        assert "unverified thing" in document[-1].get_text()
        document.close()

    def test_a_quote_that_cannot_be_found_is_listed_too(self):
        """Verified against the Markdown, but not findable in the page text."""
        marked, report = annotate(make_pdf(SHORT), [risk("elsewhere", "Arbitration shall be seated in Singapore.")])

        assert report["highlighted"] == 0
        assert report["listed_only"] == 1

        document = pymupdf.open(stream=marked, filetype="pdf")
        assert "elsewhere" in document[-1].get_text()
        document.close()

    def test_a_risk_with_no_quote_at_all_is_listed(self):
        """Advice stored before quotes existed."""
        _, report = annotate(make_pdf(SHORT), [KeyRisk(description="old risk", severity=RiskSeverity.HIGH)])

        assert report["listed_only"] == 1

    def test_no_appendix_page_when_everything_was_placed(self):
        before = len(pymupdf.open(stream=make_pdf(SHORT), filetype="pdf"))
        marked, _ = annotate(make_pdf(SHORT), [risk("liability is unlimited", SHORT)])

        document = pymupdf.open(stream=marked, filetype="pdf")
        assert len(document) == before
        document.close()

    def test_severity_picks_the_colour(self):
        marked, _ = annotate(make_pdf(SHORT), [risk("x", SHORT, "critical")])

        document = pymupdf.open(stream=marked, filetype="pdf")
        annotation = next(document[0].annots())
        # critical is the red end of the palette, not the blue one
        assert annotation.colors["stroke"][0] > annotation.colors["stroke"][2]
        document.close()

    def test_a_clean_contract_comes_back_unmarked(self):
        marked, report = annotate(make_pdf(SHORT), [])

        assert report == {"risks": 0, "highlighted": 0, "listed_only": 0}
        assert annotations(marked) == []

    def test_many_unplaced_risks_spill_onto_another_page(self):
        risks = [risk(f"risk number {index}", "not in the document at all", verified=False) for index in range(40)]

        marked, report = annotate(make_pdf(SHORT), risks)

        assert report["listed_only"] == 40
        document = pymupdf.open(stream=marked, filetype="pdf")
        # one original page plus more than one appendix page
        assert len(document) > 2
        document.close()


# --- the route ------------------------------------------------------------


@pytest.fixture
def client(s3):
    return TestClient(app)


@pytest.fixture
def reviewed(s3, settings):
    """A project with one reviewed document in the bucket."""
    project = create_project("Acme", settings)
    pdf_key = f"{project.prefix}services-aaaaaaaa.pdf"

    s3.objects[(settings.s3_projects, pdf_key)] = make_pdf(SHORT)
    s3.objects[(settings.s3_projects, advice_key(pdf_key))] = json.dumps(
        {
            "schema_version": 2,
            "task_id": "t",
            "pdf_key": pdf_key,
            "summary": "A services agreement.",
            "key_risks": [
                {
                    "description": "liability is unlimited",
                    "severity": "critical",
                    "location": "clause 6.1",
                    "quote": SHORT,
                    "page": 1,
                    "confidence": 0.95,
                    "category": "liability",
                    "recommended_action": "Negotiate a liability cap.",
                    "quote_verified": True,
                }
            ],
            "review_decision": ReviewDecision.AUTO_APPROVED.value,
            "question": "",
        }
    ).encode()

    return project, pdf_key


def test_the_route_returns_an_annotated_pdf(client, reviewed):
    project, pdf_key = reviewed

    response = client.get("/legal/t/annotated", params={"pdf_key": pdf_key, "project_id": project.id})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-risks-highlighted"] == "1"
    assert "services-aaaaaaaa-reviewed.pdf" in response.headers["content-disposition"]
    assert annotations(response.content)[0]["content"].startswith("CRITICAL")


def test_a_key_outside_the_project_is_refused(client, reviewed):
    """Otherwise another project's documents are one query string away."""
    project, _ = reviewed

    response = client.get(
        "/legal/t/annotated",
        params={"pdf_key": "someone-elses-project/secret.pdf", "project_id": project.id},
    )

    assert response.status_code == 400


def test_a_document_with_no_review_is_404(client, reviewed):
    project, _ = reviewed

    response = client.get(
        "/legal/t/annotated",
        params={"pdf_key": f"{project.prefix}never-reviewed.pdf", "project_id": project.id},
    )

    assert response.status_code == 404
