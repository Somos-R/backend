"""Last resort: remove the second factor of a Somos R account (lost device AND lost recovery codes,
with nobody else in Somos R able to reset it from the backoffice).

    docker compose exec app poetry run python scripts/reset_platform_mfa.py --email persona@somosr.co

The person signs in again with their password and enrolls a new authenticator. Every session of the
account ends now, and the reset is written to the audit trail.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.domains.audit import service as audit  # noqa: E402
from app.domains.audit.actions import Action  # noqa: E402
from app.domains.auth import service as auth_service  # noqa: E402
from app.domains.auth.models import MfaCredential, RecoveryCode  # noqa: E402
from app.domains.users.models import User  # noqa: E402


def reset_platform_mfa(db, email: str) -> User:
    """Raises ValueError when the address is not a Somos R account."""
    user = db.scalars(select(User).where(func.lower(User.email) == email.strip().lower())).first()
    if user is None or user.user_type_code != "platform":
        raise ValueError("No existe una cuenta de Somos R con ese correo")
    db.execute(delete(MfaCredential).where(MfaCredential.user_id == user.id))
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    auth_service.revoke_all_sessions(db, user)
    audit.record(db, Action.ADMIN_MFA_RESET, target_type="user", target_id=user.id, details={"via": "script"})
    db.commit()
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            user = reset_platform_mfa(db, args.email)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    print(f"Segundo factor restablecido para {user.email}. Deberá configurarlo de nuevo al iniciar sesión.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
