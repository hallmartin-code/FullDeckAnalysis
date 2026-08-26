"""Tests for the report email.

Nothing here touches the network: transports are stubbed, and `conftest.py` strips
real credentials from the environment for every test in the suite.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from models.schemas import (  # noqa: E402
    CategoryScore,
    Finding,
    Recommendation,
    ReviewResult,
)
from notifications import send_review_notification  # noqa: E402
from notifications.render import (  # noqa: E402
    build_html,
    build_subject,
    build_text,
    escape,
)
from notifications.settings import DEFAULT_RECIPIENT, EmailSettings  # noqa: E402
from notifications.transport import (  # noqa: E402
    NullTransport,
    ResendTransport,
    SendResult,
    _describe_failure,
    build_transport,
    encode_attachment,
)


@pytest.fixture
def review():
    return ReviewResult(
        company="Nimbus Health",
        sector="Healthcare IT",
        stage="Seed",
        slide_count=14,
        presentation_score=66,
        overall_assessment="Moderate",
        investment_thesis="Care coordination for community hospitals.",
        presentation_analysis="Lean and readable, one dense slide.",
        bottom_line="Worth a first meeting.",
        analysis_source="anthropic:claude-opus-5",
        strengths=[Finding("ARR grew to $1.4M with 128% NRR.", slides=[7],
                           why_investors_care="Retention proves value.")],
        weaknesses=[Finding("No defensibility slide.", evidence="NOT FOUND",
                            why_investors_care="Moat is unproven.")],
        priority_improvements=[
            Recommendation("HIGH", "Replace the top-down TAM.", "Investors distrust it."),
            Recommendation("MEDIUM", "Add an outcomes slide.", "Proves ROI."),
            Recommendation("LOW", "Tighten slide 11.", "Density."),
        ],
        category_scores=[CategoryScore("Traction", 80, 15, "Strong"),
                         CategoryScore("Market Opportunity", 35, 10, "Top-down")],
    )


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

def test_defaults_target_the_configured_inbox():
    settings = EmailSettings.from_env()
    assert settings.recipients == [DEFAULT_RECIPIENT]
    assert "tencapital.group" in settings.sender


def test_disabled_without_a_key_and_says_why():
    settings = EmailSettings.from_env()
    assert settings.enabled is False
    assert "RESEND_API_KEY" in settings.disabled_reason


def test_enabled_once_a_key_is_present(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    assert EmailSettings.from_env().enabled is True


def test_recipient_list_is_split_on_commas_and_semicolons(monkeypatch):
    monkeypatch.setenv("REPORT_EMAIL_TO", "a@x.com, b@x.com; c@x.com")
    assert EmailSettings.from_env().recipients == ["a@x.com", "b@x.com", "c@x.com"]


def test_attachment_flags_are_parsed(monkeypatch):
    monkeypatch.setenv("REPORT_EMAIL_ATTACH_JSON", "false")
    settings = EmailSettings.from_env()
    assert settings.attach_pdf is True
    assert settings.attach_json is False


def test_build_transport_picks_null_when_disabled():
    assert isinstance(build_transport(EmailSettings()), NullTransport)


def test_build_transport_picks_resend_when_configured():
    assert isinstance(build_transport(EmailSettings(api_key="re_x")), ResendTransport)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def test_subject_carries_company_score_and_band(review):
    subject = build_subject(review)
    assert "Nimbus Health" in subject
    assert "66/100" in subject
    assert "Moderate" in subject


def test_html_avoids_layout_css_email_clients_cannot_render(review):
    html = build_html(review)
    for banned in ("display:flex", "display:grid", "<style", "position:absolute"):
        assert banned not in html


def test_html_contains_the_substance(review):
    html = build_html(review, {"attachments": ["report.pdf"]})
    for expected in ("Nimbus Health", "66", "Moderate", "ARR grew to $1.4M",
                     "No defensibility slide", "Replace the top-down TAM",
                     "Worth a first meeting", "report.pdf", "Traction"):
        assert expected in html


def test_html_shows_evidence_tags(review):
    html = build_html(review)
    assert "[not found]" in html
    assert "[s7]" in html


def test_html_escapes_markup_from_the_deck():
    result = ReviewResult(company="A & B <Holdings>", presentation_score=10,
                          overall_assessment="Needs Work",
                          bottom_line="Margins < plan & falling")
    html = build_html(result)
    assert "A &amp; B &lt;Holdings&gt;" in html
    assert "<Holdings>" not in html


def test_recommendations_are_numbered_continuously_across_priorities(review):
    html = build_html(review)
    assert ">1.<" in html and ">2.<" in html and ">3.<" in html


def test_text_alternative_mirrors_the_html(review):
    text = build_text(review, {"attachments": ["report.pdf"]})
    assert "NIMBUS HEALTH" in text
    assert "SCORE: 66/100 (Moderate)" in text
    assert "KEY STRENGTHS" in text
    assert "PRIORITY IMPROVEMENTS" in text
    assert "ATTACHED: report.pdf" in text
    assert "<" not in text.replace("<", "", 0) or "<td" not in text


def test_escape_helper_handles_none():
    assert escape(None) == ""


# --------------------------------------------------------------------------- #
# Attachments
# --------------------------------------------------------------------------- #

def test_attachment_is_base64_encoded(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7 fake")
    encoded = encode_attachment(path)
    assert encoded["filename"] == "report.pdf"
    assert base64.b64decode(encoded["content"]) == b"%PDF-1.7 fake"


def test_missing_or_empty_attachment_is_skipped(tmp_path):
    assert encode_attachment(tmp_path / "nope.pdf") is None
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert encode_attachment(empty) is None


def test_oversized_attachment_is_skipped(tmp_path, monkeypatch):
    import notifications.transport as transport_module

    monkeypatch.setattr(transport_module, "MAX_ATTACHMENT_BYTES", 10)
    path = tmp_path / "big.pdf"
    path.write_bytes(b"x" * 100)
    assert encode_attachment(path) is None


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #

def test_notification_is_skipped_when_disabled(review):
    outcome = send_review_notification(review)
    assert outcome.sent is False
    assert "RESEND_API_KEY" in outcome.skipped_reason
    assert "skipped" in outcome.summary


def test_notification_passes_everything_to_the_transport(review, tmp_path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.7 x")
    payload = tmp_path / "r.json"
    payload.write_text('{"a":1}')

    transport = NullTransport()
    settings = EmailSettings(api_key="re_x", recipients=["Info@tencapital.group"],
                             cc=["cc@x.com"])
    send_review_notification(review, pdf_path=pdf, json_path=payload,
                             settings=settings, transport=transport,
                             source_filename="deck.pptx", origin="command line")

    message = transport.messages[0]
    assert message["to"] == ["Info@tencapital.group"]
    assert message["cc"] == ["cc@x.com"]
    assert message["attachments"] == ["r.pdf", "r.json"]
    assert "Nimbus Health" in message["subject"]
    assert "deck.pptx" in message["html"]


def test_attachments_can_be_turned_off(review, tmp_path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.7 x")
    transport = NullTransport()
    settings = EmailSettings(api_key="re_x", attach_pdf=False, attach_json=False)
    send_review_notification(review, pdf_path=pdf, settings=settings,
                             transport=transport)
    assert transport.messages[0]["attachments"] == []


def test_a_broken_transport_never_breaks_the_review(review):
    class Exploding(NullTransport):
        def send(self, **_kwargs):
            raise RuntimeError("boom")

    outcome = send_review_notification(review, settings=EmailSettings(api_key="re_x"),
                                       transport=Exploding())
    assert outcome.sent is False
    assert "boom" in outcome.error


def test_network_failure_is_reported_not_raised(review, monkeypatch):
    transport = ResendTransport("re_x")
    monkeypatch.setattr(transport, "_post",
                        lambda _p: (_ for _ in ()).throw(OSError("no route")))
    outcome = transport.send(sender="a@b.com", recipients=["c@d.com"], subject="s",
                             html="<p>x</p>", text="x", attachments=[])
    assert outcome.sent is False
    assert "could not reach Resend" in outcome.error


def test_successful_send_returns_the_message_id(monkeypatch):
    transport = ResendTransport("re_x")
    monkeypatch.setattr(transport, "_post", lambda _p: (200, {"id": "msg_123"}))
    outcome = transport.send(sender="a@b.com", recipients=["c@d.com"], subject="s",
                             html="<p>x</p>", text="x", attachments=[])
    assert outcome.sent is True
    assert outcome.message_id == "msg_123"
    assert "Emailed to c@d.com" in outcome.summary


@pytest.mark.parametrize("status,expected", [
    (401, "API key"),
    (422, "verified"),
    (429, "Rate limited"),
    (500, "HTTP 500"),
])
def test_resend_errors_are_actionable(status, expected):
    assert expected in _describe_failure(status, {"message": "detail"})


def test_send_result_serializes_for_the_web_api():
    payload = SendResult(sent=True, message_id="m", recipients=["a@b.com"]).to_dict()
    assert payload["sent"] is True
    assert payload["summary"].startswith("Emailed to")


# --------------------------------------------------------------------------- #
# Integration points
# --------------------------------------------------------------------------- #

def test_cli_no_email_flag_suppresses_sending(tmp_path, monkeypatch):
    import app
    from make_sample_deck import make_pptx

    deck = make_pptx(tmp_path / "d.pptx")
    sent = []
    monkeypatch.setattr("notifications.send_review_notification",
                        lambda *a, **k: sent.append(1))

    code = app.main([str(deck), "--output", str(tmp_path / "out"),
                     "--provider", "heuristic", "--no-email", "--quiet"])
    assert code == 0
    assert sent == []


def test_cli_reports_the_email_outcome(tmp_path, capsys):
    import app
    from make_sample_deck import make_pptx

    deck = make_pptx(tmp_path / "d.pptx")
    code = app.main([str(deck), "--output", str(tmp_path / "out"),
                     "--provider", "heuristic"])
    assert code == 0
    # No key configured in tests, so it must say it skipped rather than claim success.
    assert "Email skipped" in capsys.readouterr().out


def test_web_job_exposes_the_email_outcome(tmp_path):
    from fastapi.testclient import TestClient

    from make_sample_deck import make_pptx
    from web import main as web_main

    deck = Path(make_pptx(tmp_path / "d.pptx")).read_bytes()
    client = TestClient(web_main.app)
    res = client.post("/api/review", files={"file": ("d.pptx", deck)})
    job_id = res.json()["id"]

    import time
    deadline = time.time() + 120
    while time.time() < deadline:
        body = client.get("/api/jobs/{}".format(job_id)).json()
        if body["status"] in ("done", "failed"):
            break
        time.sleep(0.4)

    assert body["status"] == "done", body.get("error")
    assert "email" in body
    assert body["email"]["sent"] is False       # no key in the test environment
    assert "summary" in body["email"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
