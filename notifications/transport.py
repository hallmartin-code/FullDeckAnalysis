"""Email delivery. Resend over plain HTTPS, plus a null transport for tests.

Sending is best-effort by design: a completed review is the product, and a mail
failure must never destroy it. Every transport therefore returns a
:class:`SendResult` rather than raising, and callers surface failures as warnings.
"""

from __future__ import annotations

import base64
import json as _json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from notifications.settings import RESEND_ENDPOINT, EmailSettings

# Resend accepts up to 40 MB per message; our reports are a few KB, so this cap only
# guards against someone pointing the attachment paths at something unexpected.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@dataclass
class SendResult:
    sent: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    recipients: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None

    @property
    def summary(self) -> str:
        if self.sent:
            return "Emailed to {}".format(", ".join(self.recipients))
        if self.skipped_reason:
            return "Email skipped: {}".format(self.skipped_reason)
        return "Email failed: {}".format(self.error or "unknown error")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sent": self.sent,
            "message_id": self.message_id,
            "error": self.error,
            "recipients": self.recipients,
            "skipped_reason": self.skipped_reason,
            "summary": self.summary,
        }


class EmailTransport(ABC):
    """Swap this to change email provider without touching callers."""

    name = "base"

    @abstractmethod
    def send(self, *, sender: str, recipients: List[str], subject: str,
             html: str, text: str, attachments: List[Dict[str, str]],
             cc: Optional[List[str]] = None,
             reply_to: Optional[str] = None) -> SendResult:
        """Deliver one message. Must not raise."""


class NullTransport(EmailTransport):
    """Records messages instead of sending them. Used by tests and dry runs."""

    name = "null"

    def __init__(self, reason: str = "email transport disabled") -> None:
        self.reason = reason
        self.messages: List[Dict[str, Any]] = []

    def send(self, *, sender: str, recipients: List[str], subject: str,
             html: str, text: str, attachments: List[Dict[str, str]],
             cc: Optional[List[str]] = None,
             reply_to: Optional[str] = None) -> SendResult:
        self.messages.append({
            "from": sender, "to": recipients, "cc": cc or [], "subject": subject,
            "html": html, "text": text,
            "attachments": [a["filename"] for a in attachments],
        })
        return SendResult(sent=False, recipients=recipients, skipped_reason=self.reason)


class ResendTransport(EmailTransport):
    """POSTs to the Resend API. Uses ``requests`` if present, else urllib."""

    name = "resend"

    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def send(self, *, sender: str, recipients: List[str], subject: str,
             html: str, text: str, attachments: List[Dict[str, str]],
             cc: Optional[List[str]] = None,
             reply_to: Optional[str] = None) -> SendResult:
        payload: Dict[str, Any] = {
            "from": sender,
            "to": recipients,
            "subject": subject,
            "html": html,
            "text": text,
        }
        if cc:
            payload["cc"] = cc
        if reply_to:
            payload["reply_to"] = reply_to
        if attachments:
            payload["attachments"] = attachments

        try:
            status, body = self._post(payload)
        except Exception as exc:  # noqa: BLE001 - network failures must not raise
            return SendResult(sent=False, recipients=recipients,
                              error="could not reach Resend: {}".format(exc))

        if 200 <= status < 300:
            message_id = None
            if isinstance(body, dict):
                message_id = body.get("id")
            return SendResult(sent=True, message_id=message_id, recipients=recipients)

        return SendResult(sent=False, recipients=recipients,
                          error=_describe_failure(status, body))

    # -- HTTP ------------------------------------------------------------- #

    def _post(self, payload: Dict[str, Any]):
        headers = {
            "Authorization": "Bearer {}".format(self.api_key),
            "Content-Type": "application/json",
        }
        try:
            import requests  # type: ignore

            response = requests.post(RESEND_ENDPOINT, headers=headers,
                                     json=payload, timeout=self.timeout)
            try:
                return response.status_code, response.json()
            except ValueError:
                return response.status_code, response.text
        except ImportError:
            pass

        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            RESEND_ENDPOINT,
            data=_json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, _json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, _json.loads(raw)
            except ValueError:
                return exc.code, raw


def _describe_failure(status: int, body: Any) -> str:
    """Turn a Resend error into something a human can act on."""
    detail = ""
    if isinstance(body, dict):
        detail = str(body.get("message") or body.get("error") or body)
    elif body:
        detail = str(body)[:300]

    if status == 401 or status == 403:
        return ("Resend rejected the API key ({}). Check RESEND_API_KEY. {}"
                .format(status, detail).strip())
    if status == 422:
        return ("Resend rejected the message ({}). The 'from' domain must be verified "
                "in the Resend dashboard. {}".format(status, detail).strip())
    if status == 429:
        return "Rate limited by Resend (429). {}".format(detail).strip()
    return "Resend returned HTTP {}. {}".format(status, detail).strip()


# --------------------------------------------------------------------------- #
# Attachment helpers
# --------------------------------------------------------------------------- #

def encode_attachment(path: Path) -> Optional[Dict[str, str]]:
    """Base64-encode a file for the Resend payload, or None if unusable."""
    try:
        path = Path(path)
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        return None
    return {
        "filename": path.name,
        "content": base64.b64encode(data).decode("ascii"),
    }


def build_transport(settings: EmailSettings) -> EmailTransport:
    if not settings.enabled:
        return NullTransport(settings.disabled_reason or "email disabled")
    return ResendTransport(settings.api_key, settings.timeout)
