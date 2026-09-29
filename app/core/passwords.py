"""Password policy shared by every flow that sets a password."""
from typing import Annotated

from pydantic import AfterValidator, EmailStr

MIN_LENGTH = 10
MAX_BYTES = 72  # bcrypt ignores (and bcrypt>=5 rejects) anything past 72 bytes

# Very common choices that satisfy the length rule; extend as needed.
COMMON_PASSWORDS = frozenset({
    "password12", "password123", "password1234", "contrasena123", "contraseña123",
    "1234567890", "12345678910", "0123456789", "qwertyuiop", "qwerty12345",
    "abcdefghij", "abc1234567", "iloveyou123", "colombia123", "bogota12345",
    "admin12345", "administrador", "welcome1234", "letmein1234", "reciclaje123",
    "somosr12345", "somos-r-2026",
})


def validate_password_strength(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres")
    if len(password.encode()) > MAX_BYTES:
        raise ValueError(f"La contraseña no puede superar {MAX_BYTES} bytes")
    if password.isdigit():
        raise ValueError("La contraseña no puede ser solo números")
    if len(set(password)) < 4:
        raise ValueError("La contraseña es demasiado repetitiva")
    if password.lower() in COMMON_PASSWORDS:
        raise ValueError("La contraseña es demasiado común")
    return password


def _normalize_email(value: str) -> str:
    return value.strip().lower()


Password = Annotated[str, AfterValidator(validate_password_strength)]
Email = Annotated[EmailStr, AfterValidator(_normalize_email)]
