# Deploying NetOps Center on Linux

Step-by-step for **Ubuntu 22.04 / 24.04 or Debian 12**, using PostgreSQL, systemd and nginx. Everything is
installed under `/opt/nms` and runs as an unprivileged `nms` user.

Replace `nms.example.com` with your server's hostname or IP address throughout.

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev git postgresql nginx \
                    iputils-ping traceroute bind9-dnsutils
```

`iputils-ping`, `traceroute` and `bind9-dnsutils` (nslookup) are used by the Network Tools page.

## 2. Create the service user and get the code

```bash
sudo useradd --system --home /opt/nms --shell /usr/sbin/nologin nms
sudo git clone https://github.com/<you>/<repo>.git /opt/nms
sudo chown -R nms:nms /opt/nms
```

## 3. Python environment

```bash
cd /opt/nms
sudo -u nms python3 -m venv .venv
sudo -u nms .venv/bin/pip install --upgrade pip
sudo -u nms .venv/bin/pip install -r requirements.txt
```

## 4. PostgreSQL database

```bash
# Pick a strong password and use it in DATABASE_URL below
sudo -u postgres psql -c "CREATE USER nms WITH PASSWORD 'change-me';"
sudo -u postgres psql -c "CREATE DATABASE nms OWNER nms;"
```

## 5. Configuration (`/opt/nms/.env`)

```bash
sudo -u nms cp .env.example .env
sudo -u nms .venv/bin/python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
sudo -u nms nano .env
sudo chmod 600 .env
```

Set at least:

```ini
DJANGO_SECRET_KEY=<the key printed above>
DJANGO_ALLOWED_HOSTS=nms.example.com
DATABASE_URL=postgres://nms:change-me@localhost:5432/nms
DJANGO_DEBUG=False
DJANGO_STATIC_ROOT=/opt/nms/staticfiles
```

## 6. Initialize the app

```bash
sudo -u nms .venv/bin/python manage.py migrate
sudo -u nms .venv/bin/python manage.py collectstatic --noinput
sudo -u nms .venv/bin/python manage.py createsuperuser
sudo -u nms .venv/bin/python manage.py check --deploy
```

## 7. systemd services

```bash
sudo cp deploy/systemd/nms-web.service deploy/systemd/nms-syslog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nms-web nms-syslog
sudo systemctl status nms-web nms-syslog
```

- `nms-web` serves the app on `127.0.0.1:8000` (only nginx can reach it).
- `nms-syslog` listens on UDP/TCP 514 (or the port set in **Settings → Syslog**). It gets the
  `CAP_NET_BIND_SERVICE` capability so it can bind to 514 without running as root.

## 8. nginx

```bash
sudo cp deploy/nginx/nms.conf /etc/nginx/sites-available/nms
sudo nano /etc/nginx/sites-available/nms          # set server_name
sudo ln -s /etc/nginx/sites-available/nms /etc/nginx/sites-enabled/nms
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Open `http://nms.example.com` and sign in with the superuser you created.

## 9. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'          # 80 + 443
sudo ufw allow 514/udp
sudo ufw allow 514/tcp
sudo ufw enable
```

If you change the syslog port in Settings, open that port instead of 514. Ideally limit syslog to your
management network, e.g. `sudo ufw allow from 10.0.99.0/24 to any port 514 proto udp`.

## 10. HTTPS (recommended)

With a public DNS name:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d nms.example.com
```

For an internal-only server, use a certificate from your internal CA in the nginx config instead.

Then in `/opt/nms/.env`:

```ini
DJANGO_SECURE=True
DJANGO_CSRF_TRUSTED_ORIGINS=https://nms.example.com
```

and `sudo systemctl restart nms-web`.

## 11. Point your network devices at the server

Cisco IOS example (the **Add network device** page and **Settings → Syslog** show the exact command):

```
logging host <server-ip>
logging trap informational
```

Add each device to the inventory with the IP address it sends syslog **from** (usually its management IP),
so its messages are linked to it. Backups and the SSH console connect to that same address on port 22.

## 12. Email alerts (optional)

In **Settings → Email**, enter your SMTP server and recipients and click **Save & send test email**.
Set `NMS_BASE_URL=https://nms.example.com` in `.env` so emails link back to the site. The server needs
outbound access to the SMTP port (usually 587 or 465).

## Updating

```bash
cd /opt/nms
sudo -u nms git pull
sudo -u nms .venv/bin/pip install -r requirements.txt
sudo -u nms .venv/bin/python manage.py migrate
sudo -u nms .venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart nms-web nms-syslog
```

## Database backups

The database holds the inventory (including device credentials), config backups and syslog. Back it up, e.g.
nightly with cron:

```bash
sudo mkdir -p /var/backups/nms && sudo chown postgres /var/backups/nms
# as root: crontab -e
0 3 * * * sudo -u postgres pg_dump -Fc nms > /var/backups/nms/nms-$(date +\%F).dump && find /var/backups/nms -mtime +14 -delete
```

Store the dumps securely; they contain device credentials.

## Troubleshooting

| Symptom | Check |
|---|---|
| 502 Bad Gateway | `sudo systemctl status nms-web` and `journalctl -u nms-web -n 50` |
| "Bad Request (400)" | The hostname you browse to isn't in `DJANGO_ALLOWED_HOSTS` |
| CSRF error on login over HTTPS | `DJANGO_CSRF_TRUSTED_ORIGINS` must include `https://your-host` |
| No syslog arriving | `journalctl -u nms-syslog -f`, the firewall, and Settings → Syslog (listener status) |
| SSH console "Connection refused" | Open it from the inventory page on the same hostname the site is served on |
| Page styles missing | Re-run `collectstatic`; check the `/static/` alias path in the nginx config |
