# ---------- Stage 1: build Angular UI (on the build machine, same files for every CPU) ----------
FROM --platform=$BUILDPLATFORM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN NG_CLI_ANALYTICS=false npx ng build --configuration production

# ---------- Stage 2: Python API + UI in one image ----------
FROM python:3.12-slim

LABEL org.opencontainers.image.title="MyAppsUptime"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/myappsuptime.db \
    STATIC_DIR=/app/static

# iputils-ping is needed for Ping monitors
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=ui /ui/dist/myappsuptime-ui/browser ./static

RUN useradd --system --uid 10001 appuser \
    && mkdir /data \
    && chown appuser /data \
    && chmod 700 /data
USER appuser

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
