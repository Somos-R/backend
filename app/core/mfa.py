"""Second factor for Somos R's own accounts: TOTP (RFC 6238) and recovery codes.

TOTP is a dozen lines over the standard library (HMAC-SHA1, 30-second steps, 6 digits: what every
authenticator app implements), and is checked against the RFC's own test vectors. The secret is
encrypted at rest with Fernet, so a database leak alone does not reveal it.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accept the previous and the next step too: clocks drift
SECRET_BYTES = 20  # 160 bits, the RFC's recommendation for SHA-1
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (i, l, o, 0, 1)


# --- TOTP -----------------------------------------------------------------------------

def generate_secret() -> str:
    """A new random secret, base32 (what authenticator apps take)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _b32decode(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)


def _code_at(secret: str, step: int) -> str:
    digest = hmac.new(_b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify_totp(secret: str, code: str, last_step: int | None = None, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None if it is wrong.

    A step at or before `last_step` is refused, so a code that was already used (or one older than
    the last accepted) cannot be replayed within its validity window.
    """
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step_now = current_step(now)
    matched = None
    for step in range(step_now - WINDOW, step_now + WINDOW + 1):
        # Compare every candidate (no early exit) in constant time.
        if hmac.compare_digest(_code_at(secret, step), code):
            matched = step
    if matched is None or (last_step is not None and matched <= last_step):
        return None
    return matched


def provisioning_uri(secret: str, account: str, issuer: str = "Somos R") -> str:
    """The `otpauth://` link an authenticator app reads (the client draws it as a QR code)."""
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}"


# --- Encryption of the secret at rest ----------------------------------------------

def _fernet() -> Fernet:
    key = settings.mfa_encryption_key
    if key:
        return Fernet(key.encode())
    # Development convenience only: settings refuse to start outside dev without a real key.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"mfa:{settings.secret_key}".encode()).digest()))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    """The plain secret, or None if it cannot be decrypted (wrong key)."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


# --- Recovery codes -----------------------------------------------------------------

def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """One-time codes shown to the person once; only their hashes are stored."""
    def one() -> str:
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(count)]


def normalize_recovery_code(code: str) -> str:
    return (code or "").strip().lower().replace("-", "").replace(" ", "")


def hash_recovery_code(code: str) -> str:
    # High-entropy random codes: a fast hash is enough (there is nothing to brute-force offline).
    return hashlib.sha256(normalize_recovery_code(code).encode()).hexdigest()
