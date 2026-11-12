# MyAppsUptime - Usage Guide

## 0. Install without building

A ready-made image is published to GitHub Container Registry on every push to `main`.

**Docker run**

```bash
docker pull ghcr.io/thangaduraiponnusamy/myappsuptime:latest
docker run -d --name myappsuptime -p 8000:8000 \
  -v myappsuptime-data:/data --restart unless-stopped \
  ghcr.io/thangaduraiponnusamy/myappsuptime:latest
```

**Docker compose** (save as `docker-compose.yml`, then `docker compose up -d`)

```yaml
services:
  myappsuptime:
    image: ghcr.io/thangaduraiponnusamy/myappsuptime:latest
    container_name: myappsuptime
    ports:
      - "8000:8000"
    volumes:
      - myappsuptime-data:/data
    restart: unless-stopped

volumes:
  myappsuptime-data:
```

Open http://localhost:8000 and sign in with `admin` / `admin`. You must change the password right away.

**Pick a version**

| Tag | Meaning |
|---|---|
| `latest` | Newest build from `main` |
| `v17` etc. | A fixed release (each new `v*` git tag) |
| `sha-<commit>` | One exact commit |

**Update to a new version** (data in the volume is kept)

```bash
docker pull ghcr.io/thangaduraiponnusamy/myappsuptime:latest
docker rm -f myappsuptime
# run the same "docker run" command again, or: docker compose up -d
```

## 1. Add a monitor

Click **+ New monitor** and pick a check type.

| Type | Target example | Checks |
|---|---|---|
| HTTP(s) | `https://example.com/health` | Status code in range (default `200-399`) |
| HTTP(s) - Keyword | URL + keyword `healthy` | Status code + keyword in the page |
| HTTP(s) - JSON | URL + path `data.status` + expected `ok` | Value at the JSON path |
| TCP Port | `db.example.com` + `5432` | Port accepts connections |
| Ping | `10.0.0.1` | ICMP reply |
| DNS | `example.com` | Name resolves |
| SSL Certificate | `example.com` | Days until expiry |
| Push / Cron | (none) | Your job calls a URL. No call in time = DOWN |

Main options:

| Option | Meaning |
|---|---|
| Interval | Seconds between checks (min 20) |
| Retries | Extra tries before DOWN |
| Depends on | Parent monitor. No alerts for this one while the parent is down |
| SLA target | For example `99.9`. Used for the error budget |
| Slow threshold | Above this (ms) = DEGRADED |
| Anomaly detection | DEGRADED when latency jumps far above normal |
| Repeat alert | Send again every N minutes while still down |
| Tags | Filter monitors and route alerts |

## 2. Status colors

| Color | Status |
|---|---|
| Green | UP |
| Red | DOWN |
| Orange | DEGRADED (slow, anomaly, or SSL expiring soon) |
| Blue | MAINTENANCE |
| Gray | PAUSED |

## 3. Monitor page

- Uptime 24h / 7d / 30d, average and P95 response time, MTTR.
- **SLA & error budget**: how much allowed downtime is left this month.
- Response time chart: 1h, 24h, 7d, 30d. Red lines = DOWN checks.
- **Check now**, **Pause / Resume**, **Edit**, **Delete**.

## 4. Push monitors (cron jobs)

After saving a Push monitor, copy the push URL from its page. Call it at the end of your job:

```bash
curl -fsS "http://<host>:8000/api/public/push/<token>?status=up&msg=OK"
```

Report a failure with `status=down`. If no call arrives within the interval, the monitor goes DOWN.

## 5. Incidents

- Opened on DOWN, closed automatically on recovery.
- Click **Acknowledge**, enter your name and a note. The team sees who is on it.
- The sidebar shows the number of unacknowledged incidents.

## 6. Alerting

Go to **Alerting** and add a channel: Microsoft Teams, Slack or generic webhook.

- No tags = alerts for all monitors.
- With tags (for example `team-payments`) = only monitors with that tag.
- Use **Send test** to check it.

Webhook JSON body: `monitor`, `status`, `message`, `text`.

## 7. Maintenance windows

Go to **Maintenance**, set start and end, select monitors (none = all).
During the window: status is MAINTENANCE, no alerts, no incidents.

## 8. Status pages and badges

- **Status pages**: choose title, slug and monitors. Share `http://<host>:8000/status/<slug>`.
- **Badges** work for monitors on a status page:

```markdown
![uptime](http://<host>:8000/api/public/badge/<id>/uptime.svg)
```

Kinds: `status.svg`, `uptime.svg`, `latency.svg`.

## 9. Security

Start with a token:

```bash
API_TOKEN=change-me docker compose up -d
```

Then open **Settings** and save the same token in the browser.
Status pages, badges and push URLs stay public.

## 10. Prometheus

Scrape `http://<host>:8000/api/metrics` (add the bearer token if set).

Metrics: `myappsuptime_monitor_up`, `myappsuptime_monitor_latency_ms`, `myappsuptime_monitor_uptime_24h`, `myappsuptime_ssl_days_left`.

## 11. Backup

- **Settings > Export JSON** saves monitors and alert channels. **Import JSON** restores them.
- Full database copy:

```bash
docker cp myappsuptime:/data/myappsuptime.db ./myappsuptime-backup.db
```

## 12. API examples

```bash
curl -X POST http://localhost:8000/api/monitors \
  -H "Content-Type: application/json" \
  -d '{"name":"Example","type":"http","target":"https://example.com","sla_target":99.9,"tags":["prod"]}'

curl http://localhost:8000/api/monitors
curl http://localhost:8000/api/monitors/1/stats
```

Full API docs: http://localhost:8000/docs
