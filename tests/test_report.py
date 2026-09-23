"""The emailed report: what it says, and that model output cannot inject markup
into it."""

import json
from html import escape

import pytest

from enums.ReviewDecision import ReviewDecision
from enums.RiskSeverity import RiskSeverity
from exceptions.notification import EmailNotConfiguredError, EmailSendError
from schemas.key_risk import KeyRisk
from schemas.legal_advice import LegalAdvice
from schemas.legal_review import DocumentAdvice
from utils.mailer import build_message, is_configured, send_email
from utils.report import render_html, render_json, severity_counts, subject_for


def document(pdf_key="contract-a1b2c3d4.pdf", **advice_kwargs) -> DocumentAdvice:
    advice = LegalAdvice(
        summary=advice_kwargs.pop("summary", "A services agreement."),
        key_risks=advice_kwargs.pop(
            "key_risks",
            [
                KeyRisk(
                    description="Unlimited liability",
                    severity=RiskSeverity.HIGH,
                    location="clause 9",
                    quote="The Supplier's liability is unlimited.",
                    page=5,
                    quote_verified=True,
                ),
                KeyRisk(description="Short notice period", severity=RiskSeverity.LOW),
            ],
        ),
        **advice_kwargs,
    )

    return DocumentAdvice(pdf_key=pdf_key, advice=advice)


# --- the body ------------------------------------------------------------


def test_the_report_names_every_document_and_risk():
    html = render_html("abc123", [document(), document("nda-9f8e7d6c.pdf")])

    assert html.count("contract-a1b2c3d4.pdf") == 1
    assert html.count("nda-9f8e7d6c.pdf") == 1
    assert html.count("Unlimited liability") == 2


def test_the_totals_count_every_severity():
    counts = severity_counts([document(), document()])

    assert counts == {RiskSeverity.HIGH: 2, RiskSeverity.LOW: 2}


def test_risks_are_listed_worst_first():
    html = render_html("abc123", [document()])

    assert html.index("Unlimited liability") < html.index("Short notice period")


def test_the_quote_and_page_are_included():
    """The quote is escaped, apostrophe and all, before it reaches the mail client."""
    html = render_html("abc123", [document()])

    assert escape("The Supplier's liability is unlimited.") in html
    assert "p. 5" in html


def test_an_unverified_quote_says_so():
    risk = KeyRisk(description="d", severity=RiskSeverity.HIGH, quote="Nowhere in the document.", quote_verified=False)
    html = render_html("abc123", [document(key_risks=[risk])])

    assert "unverified quote" in html


def test_advice_nobody_signed_off_is_flagged():
    html = render_html("abc123", [document(review_decision=ReviewDecision.UNREVIEWED_TIMEOUT)])

    assert "Treat this advice as a draft" in html


def test_the_project_name_appears_when_there_is_one():
    assert "Acme NDAs" in render_html("abc123", [document()], project_name="Acme NDAs")


def test_model_output_cannot_inject_markup():
    """A risk description is untrusted text, and this one goes in an email."""
    risk = KeyRisk(description="<script>alert(1)</script>", severity=RiskSeverity.HIGH)
    html = render_html("abc123", [document(key_risks=[risk])])

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_document_with_no_risks_says_so():
    assert "No key risks flagged" in render_html("abc123", [document(key_risks=[])])


# --- the subject ---------------------------------------------------------


def test_the_subject_reports_the_worst_severity():
    subject = subject_for([document()])

    assert "2 document" not in subject
    assert "worst high" in subject


def test_the_subject_says_when_nothing_was_flagged():
    assert "none flagged" in subject_for([document(key_risks=[])])


def test_the_subject_names_the_project():
    assert "Acme" in subject_for([document()], project_name="Acme")


# --- the attachment ------------------------------------------------------


def test_the_attachment_is_the_same_advice_the_api_serves():
    payload = json.loads(render_json("abc123", [document()]))

    assert payload["task_id"] == "abc123"
    assert payload["documents"][0]["key_risks"][0]["quote_verified"] is True


# --- the mailer ----------------------------------------------------------


def test_nothing_is_sent_without_a_server(settings):
    assert not is_configured(settings)

    with pytest.raises(EmailNotConfiguredError):
        send_email("legal@acme.test", "Subject", "<p>hi</p>", settings)


def test_the_message_carries_both_parts_and_the_attachment(settings, monkeypatch):
    monkeypatch.setattr(settings, "smtp_from", "agent@acme.test")

    message = build_message(
        "legal@acme.test",
        "Legal review",
        "<p>hi</p>",
        settings,
        [("advice.json", b"{}", "json")],
    )

    assert message["To"] == "legal@acme.test"
    assert message["From"] == "agent@acme.test"
    assert message.get_body(("html",)) is not None
    [attachment] = list(message.iter_attachments())
    assert attachment.get_filename() == "advice.json"


def test_a_refused_message_becomes_an_email_send_error(settings, monkeypatch):
    import smtplib

    monkeypatch.setattr(settings, "smtp_host", "smtp.acme.test")
    monkeypatch.setattr(settings, "smtp_from", "agent@acme.test")

    class Refusing:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def send_message(self, message):
            raise smtplib.SMTPRecipientsRefused({})

    monkeypatch.setattr(smtplib, "SMTP", Refusing)

    with pytest.raises(EmailSendError, match="could not send"):
        send_email("legal@acme.test", "Subject", "<p>hi</p>", settings)
