# NetOps Center

A self-hosted network management system for switches: inventory, a built-in syslog server, configuration
backup history with diffs, an in-browser SSH console, and network diagnostics, all in one dark "NOC" web UI.

Built with Django 5.2 (LTS), Channels/Daphne and PostgreSQL.

## Features

| Area | What it does |
|---|---|
| **Dashboard** | Switch count, backup health (succeeded / failed / never), switches whose configuration changed in the last 24h, syslog volume per hour and by severity. |
| **Inventory** | Switches with vendor, location, status and last backup. Click a hostname to open its backup history. |
| **SSH console** | The terminal icon opens a full xterm session to the switch in a new window, using the stored credentials. Supports older switches that only offer legacy SSH algorithms. |
| **Syslog server** | Listens on UDP + TCP (port configurable in Settings, default 514). Live view with pattern / regex search, severity filter and auto-refresh (5s, 10s, 30s, 1 min). Detects config-change messages from Cisco, Arista, NX-OS, Juniper and Aruba/HPE. |
| **Backups** | Per-switch history: view a full configuration, or pick two backups and see added / removed lines. *(The automatic backup job is on the roadmap; the history, compare and schedule settings are ready.)* |
| **Network tools** | Ping, traceroute and nslookup run from the server, with output streamed live. |
| **Settings** | Change password, syslog port and retention, backup schedule, email notification settings. |

## Architecture

```
Browser ──HTTP/WebSocket──> nginx ──> Daphne (Django ASGI app) ──SSH──> switches
                                           │
Switches ──syslog UDP/TCP 514──> syslog listener (manage.py syslog_server)
                                           │
                                     PostgreSQL
```

Two processes share the database:

- **Web app**: Django served by Daphne. Normal pages over HTTP; the SSH console over a WebSocket.
- **Syslog listener**: an asyncio UDP/TCP server that batches messages into the database once a second. It
  reads its port and retention from Settings and switches ports live when they change.

## Local development

Requires Python 3.10+.

```bash
git clone https://github.com/<you>/<repo>.git
cd <repo>
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# In .env set DJANGO_DEBUG=True and remove the DATABASE_URL line to use SQLite

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver            # http://127.0.0.1:8000
python manage.py syslog_server        # in a second terminal
```

Useful commands:

```bash
python manage.py syslog_test [--tcp] [--port 514]   # send sample syslog messages to the listener
python manage.py demo_backups [hostname ...]        # sample backup history to try compare/view
python manage.py demo_backups --clear               # remove the sample backups
python manage.py test                               # run the test suite
```

## Configuration

All settings are environment variables, read from `.env` in the project root. See [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(required unless DEBUG)* | Django secret key |
| `DJANGO_DEBUG` | `False` | Detailed error pages; local development only |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Hostnames/IPs the site is served on |
| `DATABASE_URL` | SQLite `db.sqlite3` | e.g. `postgres://nms:pass@localhost:5432/nms` |
| `DJANGO_SECURE` | `False` | Set `True` once served over HTTPS |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | – | e.g. `https://nms.example.com` |
| `NMS_ALLOW_SIGNUP` | `False` | Allow public account registration |
| `DJANGO_TIME_ZONE` | `UTC` | Server time zone (the UI shows each viewer's local time) |

## Deployment

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for a step-by-step guide for Ubuntu/Debian with PostgreSQL, systemd and
nginx. The service and site files are in [`deploy/`](deploy/).

## Security notes

- **Switch credentials are stored in the database unencrypted.** Restrict access to the server and database,
  and use dedicated switch accounts with only the privileges NetOps Center needs. Encrypting them is on the roadmap.
- **SSH host keys are not verified yet.** The console connects to whatever answers at a switch's IP.
- Public sign-up is **off** by default; create users with `createsuperuser` or the admin site (`/admin/`).
- Every signed-in user can see all switches and change system settings.
- Serve the site over HTTPS (see DEPLOYMENT.md) so passwords and console sessions are encrypted in transit.

## Roadmap

- Automatic configuration backups over SSH on the configured schedule
- Encrypted storage of switch credentials; SSH host key pinning
- Email notifications (settings already available)
- Edit / delete switches from the inventory
- Roles: read-only operators vs administrators
