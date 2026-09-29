"""Delete expired token rows. Meant for a daily cron (Railway cron, GitHub Actions, etc.).

    docker compose exec app poetry run python scripts/purge_expired_tokens.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal  # noqa: E402
from app.domains.auth.maintenance import purge_expired  # noqa: E402


def main() -> None:
    with SessionLocal() as db:
        counts = purge_expired(db)
    for table, deleted in counts.items():
        print(f"{table}: {deleted} deleted")


if __name__ == "__main__":
    main()
