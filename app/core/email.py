"""Outgoing email. One function, three backends chosen by settings.email_backend.

Sending never raises into the request: a provider outage must not turn a
registration or a password reset into a 500. Failures are logged.
"""
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


# Only used by the "memory" backend (tests).
outbox: list[Email] = []


def send_email(to: str, subject: str, body: str) -> None:
    message = Email(to=to, subject=subject, body=body)
    backend = settings.email_backend

    if backend == "memory":
        outbox.append(message)
    elif backend == "resend":
        _send_with_resend(message)
    else:
        # Development: the log is the inbox. Contains the one-time link.
        logger.warning("EMAIL to=%s subject=%s\n%s", to, subject, body)


def _send_with_resend(message: Email) -> None:
    if not settings.email_api_key:
        logger.error("EMAIL_BACKEND=resend but EMAIL_API_KEY is not set; dropping email to %s", message.to)
        return
    try:
        response = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {settings.email_api_key}"},
            json={
                "from": settings.email_from,
                "to": [message.to],
                "subject": message.subject,
                "text": message.body,
            },
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to send email to %s", message.to)
