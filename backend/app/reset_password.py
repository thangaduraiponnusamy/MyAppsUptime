"""Reset a user's password from the server shell: python -m app.reset_password <user> <new-password>"""
import sys

from sqlalchemy import func, select

from .auth import end_all_sessions, hash_password
from .database import SessionLocal, upgrade_schema
from .models import User


def main() -> None:
    if len(sys.argv) != 3 or len(sys.argv[2]) < 8:
        sys.exit("Usage: python -m app.reset_password <user> <new-password (8+ chars)>")
    upgrade_schema()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(func.lower(User.username) == sys.argv[1].lower()))
        if user is None:
            sys.exit(f"User '{sys.argv[1]}' not found")
        user.password_hash = hash_password(sys.argv[2])
        user.must_change_password = False
        user.active = True
        end_all_sessions(db, user.id)
        db.commit()
    print(f"Password for '{sys.argv[1]}' was reset")


if __name__ == "__main__":
    main()
