from app import main


def test_production_needs_security_settings(monkeypatch):
    for name in ("SECRET_KEY", "APP_URL", "API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "DB_PASSWORD", "")
    assert len(main.production_problems()) == 3

    monkeypatch.setattr(main, "DB_PASSWORD", "d" * 16)
    monkeypatch.setenv("SECRET_KEY", "s" * 32)
    monkeypatch.setenv("APP_URL", "http://uptime.example.com")
    monkeypatch.setenv("API_TOKEN", "short")
    assert [p.split()[0] for p in main.production_problems()] == ["APP_URL", "API_TOKEN"]

    monkeypatch.setenv("APP_URL", "https://uptime.example.com")
    monkeypatch.delenv("API_TOKEN")
    assert main.production_problems() == []

    monkeypatch.setenv("SECRET_KEY", "d" * 16 + "x" * 16)
    monkeypatch.setattr(main, "DB_PASSWORD", "d" * 16 + "x" * 16)
    assert main.production_problems() == ["SECRET_KEY and DB_PASSWORD must be different"]
