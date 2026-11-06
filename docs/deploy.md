# Deployment on one server

SunSync runs on one Linux server without Docker. Caddy serves the web app over https and forwards `/api` to the API and `/measure` to the measurement server. The files are in `deploy/`.

```
phone / PC --https--> Caddy (443)
                       |- /api/*      -> API (uvicorn, 127.0.0.1:8000) -- /var/lib/sunsync/sunsync.db
                       |- /measure/*  -> measurement server (uvicorn, 127.0.0.1:8001)
                       '- everything else -> built web app in /var/www/sunsync
  every hour: forecast worker --> same database file
```

Why https: the QR code that opens the phone app is only shown for an `https` address that is not `localhost`, and phone browsers usually allow the camera only on `https`.

## Layout on the server

| Path | Content |
|---|---|
| `/opt/sunsync` | The repository (owned by root; the services only read it) |
| `/opt/sunsync/.venv` | Python environment shared by the API, the worker and the measurement server |
| `/etc/sunsync/sunsync.env` | Settings, mode 600, never committed |
| `/var/lib/sunsync` | `sunsync.db` and `backups/` (the only place the services write) |
| `/var/www/sunsync` | The built web app |

## One-time setup

The steps assume Ubuntu 24.04 (Python 3.12). The project needs Python 3.11 or newer and Node 22.

1. **Packages.** `libgl1` and `libglib2.0-0` are the system libraries OpenCV usually needs on a server.

   ```bash
   sudo apt update
   sudo apt install -y git rsync python3-venv python3-pip curl libgl1 libglib2.0-0
   ```

2. **Node 22** (the web app is built on the server; check with `node -v`) and **Caddy**: follow https://nodejs.org/en/download and https://caddyserver.com/docs/install#debian-ubuntu-raspbian.

3. **User, folders, code.**

   ```bash
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin sunsync
   sudo mkdir -p /etc/sunsync /var/lib/sunsync /var/www/sunsync
   sudo chown sunsync:sunsync /var/lib/sunsync
   sudo git clone https://github.com/hing9-9-9/SunSync.git /opt/sunsync
   sudo python3 -m venv /opt/sunsync/.venv
   ```

4. **Settings.** Copy `deploy/env.example` to `/etc/sunsync/sunsync.env`, replace `YOUR_NAME`, and `chmod 600` it.

5. **Services.**

   ```bash
   sudo cp /opt/sunsync/deploy/*.service /opt/sunsync/deploy/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable sunsync-api sunsync-measure sunsync-worker.timer sunsync-backup.timer
   sudo /opt/sunsync/deploy/update.sh      # installs, builds the web app and starts the services
   sudo systemctl start sunsync-worker.timer sunsync-backup.timer
   ```

6. **Address and https.** Create a free name at https://www.duckdns.org and point it to the server's public IP. Open ports 80 and 443 (and 22 only for your own IP) in the AWS security group; Caddy needs 80 and 443 to get the certificate. Then:

   ```bash
   sudo cp /opt/sunsync/deploy/Caddyfile /etc/caddy/Caddyfile
   caddy hash-password                      # prints the hash for YOUR_PASSWORD_HASH
   sudo nano /etc/caddy/Caddyfile           # set YOUR_NAME, YOUR_USER, YOUR_PASSWORD_HASH
   sudo systemctl reload caddy
   ```

   While you are still trying things out, use the Let's Encrypt staging endpoint (see the Caddy docs) so that failed attempts do not use up the real rate limit.

## Update the server

```bash
sudo /opt/sunsync/deploy/update.sh
```

It fast-forwards to `origin/main`, installs the packages, builds the web app, copies it to `/var/www/sunsync` and restarts the API and the measurement server. Do not deploy on the day before a demo (code freeze: 11/19 and 12/10).

## Check that it works

| Check | How |
|---|---|
| Services are running | `systemctl status sunsync-api sunsync-measure` |
| Timers are set | `systemctl list-timers 'sunsync-*'` |
| API answers | `curl -u USER:PASSWORD https://YOUR_NAME.duckdns.org/api/health` |
| Measurement server answers | `curl -u USER:PASSWORD https://YOUR_NAME.duckdns.org/measure/health` |
| Forecast worker works | `sudo systemctl start sunsync-worker.service && journalctl -u sunsync-worker -n 5` |
| Backup works | `sudo systemctl start sunsync-backup.service && ls /var/lib/sunsync/backups` |
| Survives a reboot | `sudo reboot`, then open the address again |
| Phone | Open the address on a phone, create a space on the PC, press "Capture on phone" and scan the QR code |

## Backup and logs

- A daily timer copies the database to `/var/lib/sunsync/backups` and keeps the 14 newest copies. The copy uses SQLite's own backup, because the database runs in WAL mode and a plain file copy can be broken. Download a copy now and then with `scp`; a backup on the same disk does not survive losing the server.
- Logs: `journalctl -u sunsync-api -u sunsync-measure -u caddy`.

## Notes

- **Photos are not stored.** The measurement server receives each photo as base64 inside the request, uses it in memory and returns only the detected markers; no code writes it to disk. Requests are limited to 16 MB by the server and 20 MB by Caddy.
- **Forecast worker.** The API works without it (it falls back to the clear-sky value), but the forecast comparison needs it. It runs once an hour, only for the building locations saved in spaces, and calls Open-Meteo, so the server needs outbound internet access.
- **Basic auth** in the `Caddyfile` closes the site until the Google login ships. A browser may fetch the web app manifest without the password, which can stop the phone from offering to install the app while basic auth is on; in that case remove the block and share the address only inside the team.
- **Remove the server after the last demo (12/11)**: delete the instance and the public IP in the AWS console and download a last backup first. Set an AWS budget alert when you create the account.

## Not verified yet

These were written without a real server and have to be confirmed on it: the certificate for the free address, the server size (memory with OpenCV and pvlib together), that the OpenCV libraries are enough, and the phone camera over https.
