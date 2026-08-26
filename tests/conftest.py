"""Shared test setup.

The suite must never spend money or send mail. ``.env`` is loaded by both the CLI and
the web app at import time, so a developer with real credentials on disk would
otherwise fire live Anthropic calls and real Resend emails just by running pytest.
The autouse fixture below strips those credentials from every test's environment.

A test that genuinely needs credentials must set them itself, explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

LIVE_CREDENTIALS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "RESEND_API_KEY",
)


@pytest.fixture(autouse=True)
def no_live_calls(monkeypatch):
    """Strip provider credentials so nothing reaches the network."""
    for name in LIVE_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    # Keep addressing deterministic regardless of the developer's .env.
    monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)
    monkeypatch.delenv("REPORT_EMAIL_FROM", raising=False)
    monkeypatch.delenv("REPORT_EMAIL_CC", raising=False)
