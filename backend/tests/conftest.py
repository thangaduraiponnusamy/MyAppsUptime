import os
import tempfile
from pathlib import Path

# Must be set before the app is imported
DATA = Path(tempfile.mkdtemp(prefix="mau-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(DATA / 'main.db').as_posix()}"
os.environ["BACKUP_DIR"] = str(DATA / "backups")
for name in ("SECRET_KEY", "DB_PASSWORD", "DB_PASSWORD_FILE", "API_TOKEN", "APP_URL", "TENANT_DATA_DIR"):
    os.environ.pop(name, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import tenancy  # noqa: E402
from app.auth import ensure_default_admin  # noqa: E402
from app.database import upgrade_schema  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "Admin12345"


@pytest.fixture(scope="session")
def client():
    # No lifespan: the scheduler must not run during tests
    upgrade_schema()
    tenancy.load()
    with tenancy.session_for(tenancy.DEFAULT) as db:
        ensure_default_admin(db)
    c = TestClient(app)
    token = c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    c.post("/api/auth/password", json={"current": "admin", "new": PASSWORD}, headers=auth(token))
    c.token = token
    return c


def auth(token: str, tenant: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if tenant:
        headers["X-Tenant"] = tenant
    return headers


@pytest.fixture
def su(client):
    return auth(client.token)


@pytest.fixture
def data_dir():
    return DATA
