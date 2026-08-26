"""Email configuration, read from the environment.

Credentials never appear in source. On Railway these live in the service's Variables
tab; locally they come from ``.env``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_RECIPIENT = "Info@tencapital.group"
DEFAULT_SENDER = "TEN Capital Deck Review <reports@tencapital.group>"
RESEND_ENDPOINT = "https://api.resend.com/emails"


def _addresses(raw: Optional[str]) -> List[str]:
    """Split a comma or semicolon separated address list."""
    if not raw:
        return []
    parts = [p.strip() for chunk in raw.split(",") for p in chunk.split(";")]
    return [p for p in parts if p]


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EmailSettings:
    """Resolved email configuration. ``enabled`` is false when no key is present."""

    api_key: str = ""
    sender: str = DEFAULT_SENDER
    recipients: List[str] = field(default_factory=lambda: [DEFAULT_RECIPIENT])
    cc: List[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    attach_pdf: bool = True
    attach_json: bool = True
    timeout: float = 20.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.recipients)

    @property
    def disabled_reason(self) -> str:
        if not self.api_key:
            return "RESEND_API_KEY is not set"
        if not self.recipients:
            return "REPORT_EMAIL_TO is empty"
        return ""

    @classmethod
    def from_env(cls) -> "EmailSettings":
        return cls(
            api_key=(os.getenv("RESEND_API_KEY") or "").strip(),
            sender=(os.getenv("REPORT_EMAIL_FROM") or DEFAULT_SENDER).strip(),
            recipients=_addresses(os.getenv("REPORT_EMAIL_TO")) or [DEFAULT_RECIPIENT],
            cc=_addresses(os.getenv("REPORT_EMAIL_CC")),
            reply_to=(os.getenv("REPORT_EMAIL_REPLY_TO") or "").strip() or None,
            attach_pdf=_flag("REPORT_EMAIL_ATTACH_PDF", True),
            attach_json=_flag("REPORT_EMAIL_ATTACH_JSON", True),
            timeout=float(os.getenv("REPORT_EMAIL_TIMEOUT") or 20.0),
        )
