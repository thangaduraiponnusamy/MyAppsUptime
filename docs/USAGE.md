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

**Docker container monitors**

To check containers on the same host, give the app read access to the Docker socket. Add both options to `docker run`:

```bash
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --group-add $(stat -c %g /var/run/docker.sock) \
```

Compose (run `stat -c %g /var/run/docker.sock` and use that number):

```yaml
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    group_add:
      - "999"
```

| Error on the monitor | Fix |
|---|---|
| `Docker socket /var/run/docker.sock not found` | Add the `-v` line, then `docker rm -f` and run again (a restart is not enough) |
| `Permission denied` | Add the `--group-add` line (the app runs as user 10001) |
| Still not found, mount is listed | Remove any `--tmpfs /run` or `--tmpfs /var/run` option |
| Monitor runs on a private location | Add the same two options to the probe container |
| Rootless Docker | Mount its socket instead, e.g. `-v /run/user/1000/docker.sock:/var/run/docker.sock:ro` |

The socket gives full control of Docker, even with `:ro`. Add it only if you use Docker monitors.

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
| Security headers | `https://example.com` | Grade A-F for HTTPS, TLS and security headers (see 3) |
| TCP Port | `db.example.com` + `5432` | Port accepts connections |
| Ping | `10.0.0.1` | ICMP reply |
| DNS | `example.com` | Name resolves |
| SSL Certificate | `example.com` | Days until expiry |
| Push / Cron | (none) | Your job calls a URL. No call in time = DOWN |
| Docker container | `nginx` (container name or ID) | Running = UP, unhealthy = DEGRADED, stopped = DOWN. Needs the Docker socket (see [0. Docker container monitors](#0-install-without-building)) |

Main options:

| Option | Meaning |
|---|---|
| Interval | Seconds between checks (min 20) |
| Retries | Extra tries before DOWN |
| Depends on | Parent monitor. No alerts for this one while the parent is down |
| SLA target | For example `99.9`. Used for the error budget |
| Slow threshold | Above this (ms) = DEGRADED |
| Anomaly detection | DEGRADED when latency stays far above normal for 3 checks in a row |
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

### Check timing

URL monitors show **Last check timing**: one bar split into DNS, TCP connect, TLS, time to first byte (TTFB) and download.
The slowest part is the widest. Hover a red or slow beat in the status bar to see its timing.
Timings are saved for failed or slow checks only.

### Security headers monitor

Grades a URL from A to F:

| Check | Pass when |
|---|---|
| HTTPS, TLS 1.2+, strong cipher | Required. A fail here = F |
| Certificate | Not expiring within the warn days |
| HSTS | `max-age` 180 days or more |
| Content-Security-Policy | Present |
| X-Content-Type-Options | `nosniff` |
| Clickjacking | `X-Frame-Options` or CSP `frame-ancestors` |
| Referrer-Policy | Present |
| Server version | `Server` / `X-Powered-By` show no version number |

Each failed check lowers the grade by one. Below the **Minimum grade** (default B) the monitor is DEGRADED.
The monitor page shows the grade and a pass/fail list.

### Send failed checks to OpenTelemetry (optional)

Set `OTEL_EXPORTER_OTLP_ENDPOINT` (for example `http://otel-collector:4318`) and install
`opentelemetry-sdk opentelemetry-exporter-otlp-proto-http`. Each failed check is sent as one trace with a span per timing part.
`OTEL_SERVICE_NAME` sets the service name (default `myappsuptime`). Without these, nothing is sent.

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

### Acknowledge and snooze from an alert

When the app address is set, DOWN alerts get two more buttons:

| Button | What it does |
|---|---|
| Acknowledge | Marks the incident "Acknowledged by via alert link". Repeat alerts stop. |
| Snooze 1h | No alerts for this monitor for 1 hour. Checks keep running. |

- The link opens a small page; nothing changes until you press the button there.
- Links are signed, work for 7 days, and only for that one incident.
- On the monitor page you can also Acknowledge, Snooze (1h / 4h / 24h) or Unsnooze.
- All of these show up in the audit log.

### Browser push (app on phone or PC)

1. Open the app in Chrome, Edge or Firefox and choose "Install app" (optional).
2. Settings > Notifications > **Push on this device** > Enable.
3. Press **Send test**.

| Setting (env) | Default |
|---|---|
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | Created once in `/data/vapid.json` - back it up |
| `VAPID_SUBJECT` | `mailto:admin@localhost` |

- Push needs HTTPS (or `localhost`).
- You get alerts only for monitors you can see. Snooze and maintenance apply.

## 7. Maintenance windows

Go to **Maintenance**, set start and end, select monitors (none = all).
During the window: status is MAINTENANCE, no alerts, no incidents.

## 8. Status pages and badges

- **Status pages**: choose title, slug and monitors. Share `http://<host>:8000/status/<slug>`.
- **Badges** are off by default. Turn on **Enable badge** in the monitor form or on the monitor page.
  The monitor page shows previews plus Markdown and HTML to copy.

```markdown
![uptime](http://<host>:8000/api/badge/<badge-token>/uptime.svg)
```

| URL | Shows |
|-----|-------|
| `/api/badge/<token>/status.svg` | Current status |
| `/api/badge/<token>/uptime.svg?range=24h\|7d\|30d` | Uptime (default 30d) |
| `/api/badge/<token>/latency.svg` | Average response time, last 24h |

- Optional `?label=My%20API` changes the left text (max 30 characters).
- Anyone with the link can see the badge. Turning the badge off makes old links return 404; turning it on again gives a new link.

## 9. Security

What is stored and how:

| Data | Stored as |
|---|---|
| User passwords | Hashed (scrypt), cannot be read back |
| Sign-in and reset tokens | Hashed (SHA-256) |
| SMTP password | Encrypted (Fernet / AES) |
| Alert webhook URLs | Encrypted (Fernet / AES) |
| Monitors, history, settings | Plain SQLite, or fully encrypted with `DB_PASSWORD` |

### Protection settings

| Setting | Where | What it does |
|---|---|---|
| Sign-in limit | `LOGIN_IP_PER_MIN`, `LOGIN_USER_PER_MIN` (default 10) | Too many sign-ins per minute get "Too many attempts" |
| Account lock | fixed | 5 wrong passwords in a row lock the account for 15 minutes |
| Active sessions | Settings > Security | See where you are signed in, revoke one or sign out all others |
| Monitor targets | Settings > Security (super admin) | Server checks cannot reach private, local or metadata addresses. Allow private targets or add allowed networks (CIDR). Private locations are not limited |
| Metadata address | `ALLOW_METADATA=1` | Allows 169.254.169.254 (blocked by default, even with the settings above) |
| Audit log | Settings > Audit log (admin) | Every sign-in and change. Cannot be edited; secrets and passwords are never stored. Search, type filter, time range, pages of 50, CSV export. Kept `audit_keep_days` (default 365) |

### Container

The images run as user 10001 (not root). Only `/data` and `/tmp` need to be writable.
`docker-compose.yml` uses `read_only: true`, `tmpfs: /tmp`, `cap_drop: ALL` and `no-new-privileges`.
For the probe, add `--cap-add NET_RAW` if Ping monitors fail:

```bash
docker run -d --read-only --tmpfs /tmp --cap-drop ALL --cap-add NET_RAW \
  --security-opt no-new-privileges:true -e SERVER_URL=... -e PROBE_TOKEN=... myappsuptime-probe
```

### Production setup

Set `APP_ENV=production`. The app then **refuses to start** unless all of these are set:

| Variable | Rule |
|---|---|
| `DB_PASSWORD` (or `DB_PASSWORD_FILE`) | At least 16 characters. Encrypts the database |
| `SECRET_KEY` | At least 32 characters, different from `DB_PASSWORD` |
| `APP_URL` | Starts with `https://` |
| `API_TOKEN` | Empty, or at least 32 characters |

```bash
umask 077
cat > myapps.env <<EOF
APP_ENV=production
APP_URL=https://uptime.example.com
DB_PASSWORD=$(openssl rand -base64 32)
SECRET_KEY=$(openssl rand -base64 48)
TZ=Asia/Kolkata
EOF

docker run -d --name myappsuptime --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  --env-file myapps.env \
  -v myappsuptime-data:/data \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  ghcr.io/thangaduraiponnusamy/myappsuptime:latest

docker logs myappsuptime   # a missing setting is listed here
```

- `-p 127.0.0.1:8000:8000`: only the HTTPS reverse proxy on the same server can reach the app.
- Keep a copy of `myapps.env` in a password manager. Without `DB_PASSWORD` the data cannot be read.
- Needs an amd64 server (`DB_PASSWORD` is not available on arm64).
- If Ping monitors fail, add `--cap-add NET_RAW`.
- For Docker container monitors, add the two socket options from [0. Install](#0-install-without-building).

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
- **IP address** shows where the probe last connected from (public IP of that network).
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
