# NetOps Center

A self-hosted network management system for network devices: inventory, a built-in syslog server, configuration
backups with diffs, an in-browser SSH console, email alerts and network diagnostics, all in one dark "NOC" web UI.

Built with Django 5.2 (LTS), Channels/Daphne and PostgreSQL.

## Features

| Area | What it does |
|---|---|
| **Dashboard** | Network device count, backup health (succeeded / failed / never), devices whose configuration changed in the last 24h, syslog volume per hour and by severity. |
| **Inventory** | Network devices with vendor, location, status and last backup. Add, edit and delete devices; click a hostname to open its backup history. |
| **SSH console** | The terminal icon opens a full xterm session to the device in a new window, using the stored credentials. Supports older devices that only offer legacy SSH algorithms. |
| **Syslog server** | Listens on UDP + TCP (port configurable in Settings, default 514). Live view with pattern / regex search, severity filter and auto-refresh (5s, 10s, 30s, 1 min). Detects config-change messages from Cisco, Arista, NX-OS, Juniper and Aruba/HPE. |
| **Backups** | **Request backup** pulls a device's running config over SSH (Cisco IOS/NX-OS, Arista, Juniper, Aruba/HPE, MikroTik, Fortinet). Per-device history: view a full configuration, or pick two backups and see added / removed lines. Timestamps and byte counts that change on every run are ignored, so only real changes are flagged. *(Scheduled backups are on the roadmap; the schedule setting is ready.)* |
| **Network tools** | Ping, traceroute and nslookup run from the server, with output streamed live. |
| **Automation** | Select devices in the inventory and push commands to all of them: configuration mode (vendor-aware config mode and save, backup before and after with a diff per device, stop on first error) or show/exec with output collected per device. Preview with per-device variables (`{{ hostname }}`, `{{ ip_address }}`, `{{ location }}`...) and typed confirmation. Saved script library and a full push history. |
| **Email alerts** | Emails when a backup fails or a device's configuration changes (from a backup comparison or a config-change syslog message, at most once per device per 15 minutes). Includes a test-email button. |
| **Settings** | Change password, syslog port and retention, backup schedule and retention, SMTP server and notification settings. |
| **HTTPS certificate** | Settings -> Certificate: generate a private key and CSR, download it for your CA, and install the signed certificate (PEM, DER or PKCS#7, chain included). Self-signed certificates for a quick start. The private key never leaves the server; nginx reloads automatically. |

## Architecture

```
Browser ──HTTPS/WSS──> nginx (TLS) ──> Daphne (Django ASGI app) ──SSH──> network devices
                                           │
Devices ──syslog UDP/TCP 514──> syslog listener (manage.py syslog_server)
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
python manage.py nms_certificate selfsigned --cn <host-or-ip>   # certificate for HTTPS
python manage.py serve_https [--port 443]           # serve over HTTPS directly (no nginx)
```

`runserver` is plain HTTP, so set `DJANGO_SECURE=False` in `.env` while using it (secure cookies need HTTPS).

## Configuration

All settings are environment variables, read from `.env` in the project root. See [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | *(required unless DEBUG)* | Django secret key |
| `DJANGO_DEBUG` | `False` | Detailed error pages; local development only |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Hostnames/IPs the site is served on |
| `DATABASE_URL` | SQLite `db.sqlite3` | e.g. `postgres://nms:pass@localhost:5432/nms` |
| `DJANGO_SECURE` | `False` | `True` on a server: HTTPS-only cookies, HTTP redirects to HTTPS |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | – | e.g. `https://nms.example.com` |
| `NMS_ALLOW_SIGNUP` | `False` | Allow public account registration |
| `DJANGO_TIME_ZONE` | `UTC` | Server time zone (the UI shows each viewer's local time) |
| `NMS_CERT_DIR` | `./certs` | Where the HTTPS certificate and private key are kept |
| `NMS_BASE_URL` | – | e.g. `https://nms.example.com`; adds "open in NetOps Center" links to emails |

## Deployment

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for a step-by-step guide for Ubuntu/Debian with PostgreSQL, systemd and
nginx. The service and site files are in [`deploy/`](deploy/).

## Security notes

- **Device credentials are stored in the database unencrypted.** Restrict access to the server and database,
  and use dedicated device accounts with only the privileges NetOps Center needs (read access to the running
  config for backups).
- **SSH host keys are not verified yet.** The console and backups connect to whatever answers at a device's IP.
- Public sign-up is **off** by default; create users with `createsuperuser` or the admin site (`/admin/`).
- Every signed-in user can see all network devices and change system settings.
- Serve the site over HTTPS (see DEPLOYMENT.md) so passwords and console sessions are encrypted in transit.

## Roadmap

- Scheduled backups of all devices on the configured schedule
- SSH host key pinning
- Roles: read-only operators vs administrators
