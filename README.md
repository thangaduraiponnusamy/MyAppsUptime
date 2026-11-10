<img src="branding/logo-full.svg" alt="MyAppsUptime" width="420" />

# MyAppsUptime

Self-hosted uptime and SLA monitoring for your apps.
Python (FastAPI) backend + Angular frontend, shipped as **one Docker image**.

![Overview](preview/ui-overview.png)

## Why MyAppsUptime (vs Uptime Kuma)

| Feature | Uptime Kuma | MyAppsUptime |
|---|---|---|
| HTTP, keyword, TCP, ping, DNS, push | Yes | Yes |
| JSON assertion (`data.status == ok`) | Yes | Yes |
| SSL expiry tracking | Yes | Yes |
| Status pages, maintenance windows | Yes | Yes |
| **SLA target + error budget per monitor** | No | Yes |
| **Smart latency anomaly detection** | No | Yes |
| **Monitor dependencies** (no alert storm when parent is down) | No | Yes |
| **Incident acknowledge + owner + note** | No | Yes |
| **Alert routing by tag** (team channels) | No | Yes |
| **MTTR and P95 response time** | No | Yes |
| Repeat alert while down | Yes | Yes |
| Prometheus `/metrics` | Yes | Yes |
| Badges (SVG) | Yes | Yes |
| Backup / restore (JSON) | Yes | Yes |
| Light and dark theme | Yes | Yes |
| REST API with OpenAPI docs | Limited | Yes (`/docs`) |

## Quick start (Docker)

```bash
docker compose up -d --build
```

Open http://localhost:8000

Data is stored in the `myappsuptime-data` Docker volume.

### Without compose

```bash
docker build -t myappsuptime:latest .
docker run -d --name myappsuptime -p 8000:8000 -v myappsuptime-data:/data myappsuptime:latest
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `API_TOKEN` | empty (no auth) | If set, the API needs `Authorization: Bearer <token>` |
| `DATABASE_URL` | `sqlite:////data/myappsuptime.db` | SQLAlchemy database URL |
| `HEARTBEAT_RETENTION_DAYS` | `90` | Older check results are deleted |
| `CORS_ORIGINS` | `http://localhost:4200` | Only for local dev |

## Local development

Backend:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend (second terminal):

```bash
cd frontend
npm install
npm start
```

Open http://localhost:4200. API docs: http://localhost:8000/docs

## Sample UI (no backend needed)

| File | Shows |
|---|---|
| [preview/index.html](preview/index.html) | Overview dashboard (add `#dark` for dark mode) |
| [preview/monitor.html](preview/monitor.html) | Monitor details, SLA, chart |
| [preview/status.html](preview/status.html) | Public status page |

Screenshots: [ui-overview.png](preview/ui-overview.png), [ui-overview-dark.png](preview/ui-overview-dark.png), [ui-monitor.png](preview/ui-monitor.png), [ui-status.png](preview/ui-status.png).

## Folder layout

| Path | What |
|---|---|
| `backend/` | FastAPI app, checks, scheduler, models |
| `frontend/` | Angular 18 app |
| `preview/` | Static sample pages and screenshots |
| `branding/` | Logo files (SVG) and brand sheet |
| `docs/USAGE.md` | How to use the app |
| `Dockerfile` | Builds UI + API into one image |

How to use it: [docs/USAGE.md](docs/USAGE.md)
