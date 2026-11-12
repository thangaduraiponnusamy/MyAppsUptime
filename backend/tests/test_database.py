import json
import sqlite3

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import get_setting, retention_days
from app.database import make_engine, schema_version, upgrade_schema
from app.models import NotificationChannel
from app.schemas import GeneralSettings


def test_old_plain_secrets_are_encrypted(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE notification_channels (id INTEGER PRIMARY KEY, name VARCHAR(120), type VARCHAR(20), "
                "url VARCHAR(500), recipients JSON, tags JSON, project_ids JSON, active BOOLEAN)")
    con.execute("INSERT INTO notification_channels VALUES (1,'t','teams','https://hook.example/secret123','[]','[]','[]',1)")
    con.execute("CREATE TABLE app_settings (key VARCHAR(40) PRIMARY KEY, value JSON)")
    con.execute("INSERT INTO app_settings VALUES ('smtp', ?)", (json.dumps({"host": "h", "password": "smtpPW!"}),))
    con.commit()
    con.close()

    engine = make_engine(f"sqlite:///{path.as_posix()}")
    upgrade_schema(engine)
    upgrade_schema(engine)  # second run must not encrypt twice
    raw = sqlite3.connect(path)
    assert raw.execute("SELECT url FROM notification_channels").fetchone()[0].startswith("enc:")
    assert "smtpPW!" not in raw.execute("SELECT value FROM app_settings").fetchone()[0]
    with Session(engine) as db:
        assert db.get(NotificationChannel, 1).url == "https://hook.example/secret123"
        assert get_setting(db, "smtp")["password"] == "smtpPW!"
    raw.close()
    engine.dispose()


def test_migrations_add_columns(tmp_path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE incidents (id INTEGER PRIMARY KEY, monitor_id INTEGER, cause VARCHAR(300), "
                "started_at DATETIME, resolved_at DATETIME)")
    con.commit()
    con.close()
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    upgrade_schema(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("incidents")}
    assert {"note", "public_note"} <= columns
    assert {"down_after", "fail_count", "domain_expires_at"} <= {c["name"] for c in inspect(engine).get_columns("monitors")}
    assert schema_version(engine) > 0
    engine.dispose()


def test_retention():
    assert retention_days(GeneralSettings().model_dump()) == 365
    assert retention_days({"retention_value": 6, "retention_unit": "months"}) == 180
