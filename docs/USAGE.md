# MyAppsUptime - Usage Guide

## 0. Install without building

A ready-made image is published to GitHub Container Registry on every push. Use `lts` instead of `latest` if you want only stable builds.

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

To encrypt the database with a password, see [9. Security](#9-security).

**Pick a version**

| Tag | Meaning |
|---|---|
| `latest` | Newest build of any branch |
| `lts` | Newest stable build (`main`) |
| `beta` | Newest test build (`develop`) |
| `1.2.0` | Release branch `release/1.2.0` |
| `alpha-login` | Feature branch `feature/login` (not tested) |
| `20261112-1041`, `beta-...`, `1.2.0-...`, `alpha-login-...` | One exact build (UTC time) |
| `v17` etc. | A git tag |

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
| Docker container | `nginx` (container name or ID) | Running = UP, unhealthy = DEGRADED, stopped = DOWN. Needs the Docker socket (see 13) |

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
| Run from | This server, or a private location (see 13) |

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

Go to **Alerting** and add a channel:

| Channel | What you paste |
|---|---|
| Microsoft Teams | Workflow webhook URL |
| Email | Email addresses (needs the email server in Settings) |
| Telegram | Bot token + chat ID (see below) |
| Discord | Server settings > Integrations > Webhooks > Copy webhook URL |
| Google Chat | Space > Apps & integrations > Webhooks > URL |
| ntfy (phone push) | `https://ntfy.sh/<topic>` (or your own ntfy server); subscribe to the topic in the ntfy app |
| Slack / Mattermost / Rocket.Chat | Incoming webhook URL |
| Webhook | Any URL; gets JSON (fields below) |

- No tags = alerts for all monitors.
- With tags (for example `team-payments`) = only monitors with that tag.
- Use **Send test** to check it.

Telegram setup:

| Step | What to do |
|---|---|
| 1 | In Telegram, message `@BotFather`, send `/newbot`, copy the **bot token** |
| 2 | Add the bot to your group (or as admin to your channel) and send a message there |
| 3 | Open `https://api.telegram.org/bot<token>/getUpdates`, copy `"chat":{"id": ...}` as **Chat ID** |

ntfy: anyone who knows the topic name can read it, so use a long random name.

Set these in **Settings > General** so alerts look right:

| Setting | Used for |
|---|---|
| App address | **View monitor** and **Open MyAppsUptime** buttons, server link at the bottom |
| Time zone for alerts | Time shown in alerts, for example `2026-11-12 16:38:56 (Asia/Kolkata)` |

Without them, the `APP_URL` and `TZ` environment variables are used (default: no buttons, UTC).

Webhook JSON body: `monitor`, `status`, `message`, `project`, `target`, `link`, `server`, `time` (UTC), `time_local`, `text`.

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

What is stored and how:

| Data | Stored as |
|---|---|
| User passwords | Hashed (scrypt), cannot be read back |
| Sign-in and reset tokens | Hashed (SHA-256) |
| SMTP password | Encrypted (Fernet / AES) |
| Alert webhook URLs | Encrypted (Fernet / AES) |
| Monitors, history, settings | Plain SQLite, or fully encrypted with `DB_PASSWORD` |

### Database password (encrypts the whole file)

Set `DB_PASSWORD` and the whole database is encrypted (SQLCipher, AES-256). Without it the database is plain.

```bash
echo "DB_PASSWORD=$(openssl rand -base64 24)" > myapps.env
echo "SECRET_KEY=$(openssl rand -base64 32)" >> myapps.env
chmod 600 myapps.env

docker run -d --name myappsuptime -p 8000:8000 \
  --env-file myapps.env \
  -v myappsuptime-data:/data --restart unless-stopped \
  ghcr.io/thangaduraiponnusamy/myappsuptime:latest
```

- Use `--env-file` (not `-e`). With `-e` the password shows in shell history.
- Or mount a file and set `DB_PASSWORD_FILE=/run/secrets/db_pw` (Docker or Kubernetes secrets).
- An existing plain database is encrypted automatically on the next start. Then delete old `*.bak` files in `/data`; they are still plain.
- Wrong or missing password: the app stops with a clear message. Data is not touched.
- **If the password is lost, the data cannot be recovered.** Keep a copy in a password manager.
- Only Linux x86-64 (amd64) images support it.

Change the password (stop the app first):

```bash
docker stop myappsuptime
docker run --rm -it --env-file myapps.env -v myappsuptime-data:/data \
  ghcr.io/thangaduraiponnusamy/myappsuptime:latest python -m app.change_db_password
# then put the new DB_PASSWORD in myapps.env
docker rm myappsuptime   # and run the "docker run" command above again
```

### Secret key (encrypts SMTP password and webhooks)

- Set your own key (recommended):

```bash
SECRET_KEY=$(openssl rand -base64 32)   # keep this value safe
```

- If `SECRET_KEY` is not set, a key file `secret.key` is created next to the database (in `/data`).
- Back up the key. If it is lost, enter the SMTP password and webhook URLs again.
- Do not keep the key in the same backup as the database.

Without `DB_PASSWORD` the database file is not encrypted. Also protect it with:

- Disk or volume encryption (BitLocker, LUKS, encrypted EBS).
- The container runs as a non-root user and `/data` is readable only by that user.

Other tips:

- Change the `admin` / `admin` password at first sign-in.
- Run behind HTTPS (reverse proxy such as nginx, Traefik or Caddy).
- **Export JSON** contains webhook URLs in plain text. Keep the file safe.
- `API_TOKEN` is for scripts only: `Authorization: Bearer <token>`. It acts as admin.
- Status pages, badges and push URLs stay public.

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

## 13. Private locations (probes)

Check devices in another network (home LAN, office, factory) that the server cannot reach. A small **probe** runs there, picks up its monitors from the server and sends back the results. Only outgoing HTTPS from the probe is needed.

| Step | What to do |
|---|---|
| 1 | **Settings > Private locations > Add**, enter a name (for example `home`) |
| 2 | Copy the `docker run` command shown (the token is shown only once) and run it on a device in that network (Raspberry Pi, NAS, PC) |
| 3 | Edit a monitor, set **Run from** to `home` |

```bash
docker run -d --name myappsuptime-probe --restart unless-stopped --network host \
  -e SERVER_URL=https://uptime.example.com \
  -e PROBE_TOKEN='<token>' \
  ghcr.io/thangaduraiponnusamy/myappsuptime-probe:latest
```

- Works on amd64 and arm64 (Raspberry Pi 3/4/5 with a 64-bit OS).
- Location shows **online** when it called in during the last 90 seconds. If it goes offline, its monitors become PENDING and one alert is sent; another alert when it is back.
- **New token** stops the old probe. **Delete** moves its monitors back to this server.
- Push monitors always run on the server.

Docker container monitors on the probe's host: add the socket to the command above:

```bash
  -v /var/run/docker.sock:/var/run/docker.sock:ro --group-add $(stat -c %g /var/run/docker.sock) \
```

The same two options work on the server container for Docker monitors that run from the server.

Without Docker (Python 3.12):

```bash
git clone https://github.com/thangaduraiponnusamy/MyAppsUptime.git && cd MyAppsUptime
pip install -r probe/requirements.txt
SERVER_URL=https://uptime.example.com PROBE_TOKEN='<token>' python -m probe
```
