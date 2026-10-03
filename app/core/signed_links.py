"""Short-lived signed links: a payload, an expiry and an HMAC, in one URL-safe string.

Used to let a person open a private file in the browser (which cannot send an Authorization header) without
making the file public: the API decides to mint the link (and audits it), and the link stops working in minutes.
The key is derived from `SECRET_KEY` per purpose, so a link minted for one use never validates for another.
"""
import base64
import hashlib
import hmac
import time

from app.core.config import settings


class InvalidLink(Exception):
    pass


def _key(purpose: str) -> bytes:
    return hmac.new(settings.secret_key.encode(), f"signed-link:{purpose}".encode(), hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(purpose: str, payload: str, ttl_seconds: int) -> tuple[str, int]:
    """A link token for `payload` valid for `ttl_seconds`. Returns (token, expiry as a unix time)."""
    expires = int(time.time()) + ttl_seconds
    body = _b64(f"{expires}|{payload}".encode())
    signature = _b64(hmac.new(_key(purpose), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}", expires


def verify(purpose: str, token: str) -> str:
    """The payload if the token is genuine, for this purpose and not expired; otherwise InvalidLink."""
    try:
        body, signature = token.split(".")
        expected = _b64(hmac.new(_key(purpose), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise InvalidLink
        expires, payload = _unb64(body).decode().split("|", 1)
        if int(expires) < time.time():
            raise InvalidLink
        return payload
    except InvalidLink:
        raise
    except Exception as exc:  # malformed in any way
        raise InvalidLink from exc
