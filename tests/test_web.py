"""Tests for the web layer.

Uses FastAPI's TestClient and the offline heuristic provider, so no API key and no
network access are needed. Job execution is threaded, so the polling tests wait on
the real worker rather than stubbing it.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from make_sample_deck import make_pptx  # noqa: E402
from web import main as web_main  # noqa: E402
from web.jobs import DONE, FAILED, store  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(web_main.app)


@pytest.fixture(scope="module")
def deck_bytes(tmp_path_factory):
    path = make_pptx(tmp_path_factory.mktemp("web") / "sample.pptx")
    return Path(path).read_bytes()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Force the credential-free provider so tests never call a paid API."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get("/api/jobs/{}".format(job_id)).json()
        if body["status"] in (DONE, FAILED):
            return body
        time.sleep(0.4)
    raise AssertionError("job did not finish within {}s".format(timeout))


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #

def test_health_is_ok(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"


def test_index_serves_the_upload_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "TEN Capital Network" in res.text
    assert 'id="drop"' in res.text
    assert 'id="file"' in res.text
    assert 'id="go"' in res.text


def test_index_has_no_mojibake_or_stale_claims(client):
    """The page must not promise formats or addresses the server does not honour."""
    text = res_text = client.get("/").text
    for broken in ("â", "Ã‚", "Â·"):
        assert broken not in text
    # .docx is not a supported format; the accept list must not offer it.
    assert ".docx" not in text
    # Contact details are rendered from /api/config, never hard-coded.
    assert "gmail.com" not in res_text


def test_config_exposes_limits_and_never_the_key(client):
    body = client.get("/api/config").json()
    assert set(body["accepted_formats"]) == {".pdf", ".pptx", ".ppt"}
    assert body["max_upload_mb"] == 40
    assert "email_enabled" in body
    assert body["email_to"] is None          # no key configured in tests
    serialized = str(body)
    assert "sk-ant" not in serialized
    assert "api_key" not in serialized.lower()


# --------------------------------------------------------------------------- #
# Upload validation
# --------------------------------------------------------------------------- #

def test_unsupported_type_is_rejected(client):
    res = client.post("/api/review",
                      files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 415
    assert "hint" in res.json()


def test_empty_upload_is_rejected(client):
    res = client.post("/api/review",
                      files={"file": ("deck.pdf", b"", "application/pdf")})
    assert res.status_code == 400


def test_oversized_upload_is_rejected(client, monkeypatch):
    monkeypatch.setattr(web_main, "MAX_UPLOAD_BYTES", 1024)
    payload = io.BytesIO(b"x" * 5000)
    res = client.post("/api/review",
                      files={"file": ("deck.pdf", payload, "application/pdf")})
    assert res.status_code == 413


def test_errors_always_carry_error_and_hint_keys(client):
    body = client.post("/api/review",
                       files={"file": ("x.txt", b"a", "text/plain")}).json()
    assert set(body) == {"error", "hint"}


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #

def test_access_code_blocks_when_configured(client, monkeypatch, deck_bytes):
    monkeypatch.setattr(web_main, "ACCESS_CODE", "letmein")
    res = client.post("/api/review",
                      files={"file": ("d.pptx", deck_bytes)})
    assert res.status_code == 401

    res = client.post("/api/review", files={"file": ("d.pptx", deck_bytes)},
                      data={"access_code": "wrong"})
    assert res.status_code == 401


def test_access_code_accepts_the_right_value(client, monkeypatch, deck_bytes):
    monkeypatch.setattr(web_main, "ACCESS_CODE", "letmein")
    res = client.post("/api/review", files={"file": ("d.pptx", deck_bytes)},
                      data={"access_code": "letmein"})
    assert res.status_code == 202
    _wait(client, res.json()["id"])


def test_access_code_also_accepted_as_a_header(client, monkeypatch, deck_bytes):
    monkeypatch.setattr(web_main, "ACCESS_CODE", "letmein")
    res = client.post("/api/review", files={"file": ("d.pptx", deck_bytes)},
                      headers={"x-access-code": "letmein"})
    assert res.status_code == 202
    _wait(client, res.json()["id"])


# --------------------------------------------------------------------------- #
# Job lifecycle
# --------------------------------------------------------------------------- #

def test_full_review_lifecycle(client, deck_bytes):
    res = client.post("/api/review", files={"file": ("sample.pptx", deck_bytes)})
    assert res.status_code == 202
    job_id = res.json()["id"]

    body = _wait(client, job_id)
    assert body["status"] == DONE, body.get("error")

    review = body["review"]
    assert review["company"] == "Nimbus Health"
    assert 0 <= review["presentation_score"] <= 100
    assert review["priority_improvements"]
    assert review["extraction"]["slide_count"] == 14

    pdf = client.get(body["downloads"]["pdf"])
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")

    payload = client.get(body["downloads"]["json"])
    assert payload.status_code == 200
    assert payload.json()["company"] == "Nimbus Health"


def test_job_status_never_leaks_filesystem_paths(client, deck_bytes):
    res = client.post("/api/review", files={"file": ("sample.pptx", deck_bytes)})
    body = _wait(client, res.json()["id"])
    serialized = str(body)
    for leak in ("workdir", "pdf_path", "json_path", "C:\\\\", "/tmp/"):
        assert leak not in serialized


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/deadbeefdeadbeef").status_code == 404


def test_download_before_completion_is_409(client, deck_bytes):
    res = client.post("/api/review", files={"file": ("sample.pptx", deck_bytes)})
    job_id = res.json()["id"]
    early = client.get("/api/jobs/{}/report.pdf".format(job_id))
    assert early.status_code in (409, 200)  # 200 only if it finished unusually fast
    _wait(client, job_id)


def test_corrupt_deck_fails_the_job_with_a_hint(client):
    res = client.post("/api/review",
                      files={"file": ("broken.pdf", b"not a pdf at all" * 40)})
    assert res.status_code == 202
    body = _wait(client, res.json()["id"])
    assert body["status"] == FAILED
    assert body["error"]
    assert body["hint"]


def test_queue_cap_returns_503(client, monkeypatch, deck_bytes):
    monkeypatch.setattr(store, "active_count", lambda: 999)
    res = client.post("/api/review", files={"file": ("sample.pptx", deck_bytes)})
    assert res.status_code == 503


# --------------------------------------------------------------------------- #
# Template endpoint
# --------------------------------------------------------------------------- #

def test_template_endpoint_returns_a_one_page_pdf(client):
    res = client.get("/api/template.pdf")
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF-")

    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore
    doc = pymupdf.open(stream=res.content, filetype="pdf")
    try:
        assert doc.page_count == 1
        assert "[COMPANY NAME]" in doc[0].get_text()
    finally:
        doc.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
