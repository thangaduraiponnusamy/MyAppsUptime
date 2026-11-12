"""Browser tests: start the server with the built UI and drive it with Chromium."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "frontend" / "dist" / "myappsuptime-ui" / "browser"
PASSWORD = "Admin12345"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    req = urllib.request.Request(f"{base}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"{}")


@pytest.fixture(scope="session")
def server():
    if not (UI / "index.html").is_file():
        pytest.skip("Build the UI first: cd frontend && npx ng build")
    data = Path(tempfile.mkdtemp(prefix="mau-e2e-"))
    port = _free_port()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{(data / 'main.db').as_posix()}", "BACKUP_DIR": str(data / "b"),
           "TENANT_DATA_DIR": str(data), "STATIC_DIR": str(UI)}
    for name in ("DB_PASSWORD", "DB_PASSWORD_FILE", "API_TOKEN", "SECRET_KEY"):
        env.pop(name, None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                            cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{base}/health", timeout=1)
            break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("Server did not start")
    # First sign-in asks for a new password; do that here so the tests start from a normal account
    token = call(base, "POST", "/api/auth/login", {"username": "admin", "password": "admin"})["token"]
    call(base, "POST", "/api/auth/password", {"current": "admin", "new": PASSWORD}, token)
    token = call(base, "POST", "/api/auth/login", {"username": "admin", "password": PASSWORD})["token"]
    yield base, token
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(15000)
    yield page
    context.close()


def sign_in(page, base: str) -> None:
    page.goto(f"{base}/login")
    page.locator("input[name=u]").fill("admin")
    page.locator("input[name=p]").fill(PASSWORD)
    page.locator("button.btn.primary").click()
    page.get_by_role("heading", name="Dashboard").wait_for()
