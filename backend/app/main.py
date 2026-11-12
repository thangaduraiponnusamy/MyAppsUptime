import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from . import admin_api, api, dnscache, probes, tenancy, tenants_api
from .auth import ensure_default_admin
from .config import APP_VERSION, get_setting
from .database import DB_PASSWORD, upgrade_schema
from .scheduler import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def production_problems() -> list[str]:
    """Security settings that APP_ENV=production requires."""
    secret = os.getenv("SECRET_KEY", "")
    token = os.getenv("API_TOKEN", "")
    problems = []
    if len(DB_PASSWORD) < 16:
        problems.append("DB_PASSWORD (or DB_PASSWORD_FILE) must be set, at least 16 characters")
    if len(secret) < 32:
        problems.append("SECRET_KEY must be set, at least 32 characters")
    elif secret == DB_PASSWORD:
        problems.append("SECRET_KEY and DB_PASSWORD must be different")
    if not os.getenv("APP_URL", "").startswith("https://"):
        problems.append("APP_URL must be set to the https:// address of the app")
    if token and len(token) < 32:
        problems.append("API_TOKEN must be at least 32 characters (or leave it empty)")
    return problems


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("APP_ENV", "").lower() in ("prod", "production"):
        problems = production_problems()
        if problems:
            raise RuntimeError("APP_ENV=production, but:\n  - " + "\n  - ".join(problems))
    upgrade_schema()
    tenancy.load()
    for slug in tenancy.slugs(active_only=False):
        try:
            tenancy.session_for(slug).close()  # upgrades every tenant database at start
        except LookupError:
            logging.getLogger(__name__).error("Database file of tenant %s is missing", slug)
    with tenancy.session_for(tenancy.DEFAULT) as db:
        ensure_default_admin(db)
        dnscache.enable(get_setting(db, "server")["dns_cache"])
    await scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(title="MyAppsUptime", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:4200").split(","),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant"],
)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # Swagger UI at /docs loads scripts from a CDN, so it gets no CSP
    if not request.url.path.startswith(("/docs", "/redoc")):
        headers.setdefault("Content-Security-Policy", CSP)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response

app.include_router(admin_api.auth)
app.include_router(admin_api.admin)
app.include_router(tenants_api.router)
app.include_router(probes.admin)
app.include_router(probes.probe)
app.include_router(api.router)
app.include_router(api.public)


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve the built Angular UI when present (Docker image); SPA routes fall back to index.html.
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parent.parent / "static")).resolve()

if STATIC_DIR.is_dir():
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        if not path:
            page = api.page_for_host(request.headers.get("host", ""))
            if page:
                return RedirectResponse(f"/status/{page}")
        file = (STATIC_DIR / path).resolve()
        if path and file.is_file() and file.is_relative_to(STATIC_DIR):
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html")
