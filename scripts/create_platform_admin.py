"""Create a Somos R platform administrator. Run by an operator; there is no self-registration.

    docker compose exec app poetry run python scripts/create_platform_admin.py \
        --email persona@somosr.co --name "Nombre Apellido" --id-number 1234567890

The password is asked interactively (or read from PLATFORM_ADMIN_PASSWORD for automation); it is
never a command-line argument, so it does not end up in the shell history or the process list.
Later administrators are created by an existing one from the backoffice.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import TypeAdapter, ValidationError  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.passwords import Email, Password  # noqa: E402
from app.core.permissions import PLATFORM_ADMIN  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.domains.audit import service as audit  # noqa: E402
from app.domains.audit.actions import Action  # noqa: E402
from app.domains.users.models import User  # noqa: E402

MIN_LENGTH = 14  # longer than the customer policy: these are the most powerful accounts


def create_platform_admin(db, email: str, full_name: str, id_number: str, password: str) -> User:
    """Create the account and its audit entry in one transaction. Raises ValueError on bad input."""
    email = TypeAdapter(Email).validate_python(email)
    if len(password) < MIN_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres")
    try:
        TypeAdapter(Password).validate_python(password)
    except ValidationError as exc:
        raise ValueError(exc.errors()[0]["msg"]) from exc
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise ValueError("Ya existe una cuenta con ese correo")
    if db.scalar(select(User.id).where(User.id_number == id_number)):
        raise ValueError("Ya existe una cuenta con ese número de documento")

    user = User(
        email=email, full_name=full_name.strip(), id_type="CC", id_number=id_number,
        user_type_code="platform", role_code=PLATFORM_ADMIN,
        password_hash=hash_password(password),
        email_verified_at=func.now(),  # the operator vouches for the address
    )
    db.add(user)
    db.flush()
    audit.record(db, Action.PLATFORM_ADMIN_CREATED, target_type="user", target_id=user.id,
                 details={"via": "script"})
    db.commit()
    db.refresh(user)
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--id-number", required=True)
    args = parser.parse_args()

    password = os.environ.get("PLATFORM_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Contraseña: ")
        if password != getpass.getpass("Repite la contraseña: "):
            print("Las contraseñas no coinciden", file=sys.stderr)
            return 1

    with SessionLocal() as db:
        try:
            user = create_platform_admin(db, args.email, args.name, args.id_number, password)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    print(f"Administrador creado: {user.email} ({user.id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
