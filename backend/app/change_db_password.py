"""Change the database password. Stop the app first, then run with the current password set:
docker run --rm -it -v myappsuptime-data:/data -e DB_PASSWORD=<current> <image> python -m app.change_db_password"""
import getpass
import sys
from pathlib import Path

from .database import DB_PASSWORD, _is_plain, engine, key_pragma, sqlcipher
from .tenancy import DATA_DIR


def main() -> None:
    if not DB_PASSWORD:
        sys.exit("Set DB_PASSWORD (or DB_PASSWORD_FILE) to the current password first.")
    new = getpass.getpass("New password: ")
    if len(new) < 12:
        sys.exit("Use at least 12 characters.")
    if new != getpass.getpass("Repeat new password: "):
        sys.exit("Passwords do not match.")
    engine.dispose()
    files = [Path(engine.url.database), *sorted(DATA_DIR.glob("*.db"))]
    for path in files:
        if _is_plain(path):
            continue  # encrypted with the new password when the app opens it
        con = sqlcipher().connect(str(path))
        try:
            con.execute(key_pragma("key", DB_PASSWORD))
            con.execute(key_pragma("rekey", new))
        finally:
            con.close()
        print(f"Changed {path}")
    print("Done. Start the app with the new DB_PASSWORD.")


if __name__ == "__main__":
    main()
