"""Automated report email for every completed review.

    from notifications import send_review_notification

    outcome = send_review_notification(result, pdf_path=..., json_path=...)
    print(outcome.summary)

Delivery is best-effort: this never raises, so a mail problem cannot destroy a review
that already succeeded. Inspect the returned :class:`SendResult` to report the outcome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from models.schemas import ReviewResult
from notifications.render import build_html, build_subject, build_text
from notifications.settings import EmailSettings
from notifications.transport import (
    EmailTransport,
    NullTransport,
    ResendTransport,
    SendResult,
    build_transport,
    encode_attachment,
)

__all__ = [
    "EmailSettings",
    "EmailTransport",
    "NullTransport",
    "ResendTransport",
    "SendResult",
    "send_review_notification",
    "build_subject",
    "build_html",
    "build_text",
]


def send_review_notification(
    result: ReviewResult,
    pdf_path: Optional[Path] = None,
    json_path: Optional[Path] = None,
    settings: Optional[EmailSettings] = None,
    transport: Optional[EmailTransport] = None,
    source_filename: Optional[str] = None,
    origin: Optional[str] = None,
) -> SendResult:
    """Email the finished review, with the PDF and JSON attached.

    ``settings`` defaults to the environment; ``transport`` defaults to Resend when a
    key is configured and a no-op recorder otherwise. Both are injectable so tests
    never touch the network.
    """
    settings = settings or EmailSettings.from_env()
    transport = transport or build_transport(settings)

    attachments: List[Dict[str, str]] = []
    if settings.attach_pdf and pdf_path:
        encoded = encode_attachment(Path(pdf_path))
        if encoded:
            attachments.append(encoded)
    if settings.attach_json and json_path:
        encoded = encode_attachment(Path(json_path))
        if encoded:
            attachments.append(encoded)

    extra: Dict[str, Any] = {
        "attachments": [a["filename"] for a in attachments],
        "source_filename": source_filename,
        "origin": origin,
    }

    try:
        return transport.send(
            sender=settings.sender,
            recipients=list(settings.recipients),
            cc=list(settings.cc),
            reply_to=settings.reply_to,
            subject=build_subject(result),
            html=build_html(result, extra),
            text=build_text(result, extra),
            attachments=attachments,
        )
    except Exception as exc:  # noqa: BLE001 - a transport bug must not lose the review
        return SendResult(sent=False, recipients=list(settings.recipients),
                          error="email transport raised: {}".format(exc))
