# Deployment on one server

SunSync runs on one Linux server without Docker. Caddy serves the web app over https and forwards `/api` to the API and `/measure` to the measurement server. The files are in `deploy/`.

```
phone / PC --https--> Caddy (443)
                       |- /api/*      -> API (uvicorn, 127.0.0.1:8000) -- /var/lib/sunsync/sunsync.db
                       |- /measure/*  -> measurement server (uvicorn, 127.0.0.1:8001)
                       '- everything else -> built web app in /var/www/sunsync
  every hour: forecast worker --> same database file
```

Why https: phone browsers usually allow the camera only on `https`, and the QR code that opens the phone app from the PC is only shown for an `https` address that is not `localhost`. The phone app is a full client of its own: it can be opened directly at this address and used from login to measurement, and the QR code is only a shortcut that continues a space started on the PC.

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

   On a 2 GB server add swap first, so that the install and the web build do not run out of memory:

   ```bash
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

2. **Node 22** (the web app is built on the server; check with `node -v`) and **Caddy**: follow https://nodejs.org/en/download and https://caddyserver.com/docs/install#debian-ubuntu-raspbian.

3. **User, folders, code.**

   ```bash
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin sunsync
   sudo mkdir -p /etc/sunsync /var/lib/sunsync /var/www/sunsync
   sudo chown sunsync:sunsync /var/lib/sunsync
   sudo git clone https://github.com/hing9-9-9/SunSync.git /opt/sunsync   # before the pull request is merged add: --branch feat/deploy-aws
   sudo python3 -m venv /opt/sunsync/.venv
   ```

4. **Settings.** Copy `deploy/env.example` to `/etc/sunsync/sunsync.env`, replace `YOUR_NAME`, and `chmod 600` it.

5. **Services.**

   ```bash
   sudo cp /opt/sunsync/deploy/*.service /opt/sunsync/deploy/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable sunsync-api sunsync-measure sunsync-worker.timer sunsync-backup.timer
   sudo /opt/sunsync/deploy/update.sh      # installs, builds the web app and starts the services
   # before the pull request is merged: sudo env SUNSYNC_BRANCH=feat/deploy-aws /opt/sunsync/deploy/update.sh
   sudo systemctl start sunsync-worker.timer sunsync-backup.timer
   ```

6. **Address and https.** Create a free name at https://www.duckdns.org and point it to the server's public IP. Open ports 80 and 443 (and 22 only for your own IP) in the AWS security group; Caddy needs 80 and 443 to get the certificate. Then:

   ```bash
   sudo cp /opt/sunsync/deploy/Caddyfile /etc/caddy/Caddyfile
   caddy hash-password                      # prints the hash for YOUR_PASSWORD_HASH
   sudo nano /etc/caddy/Caddyfile           # set YOUR_NAME, YOUR_USER, YOUR_PASSWORD_HASH
   caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

   While you are still trying things out, use the Let's Encrypt staging endpoint (see the Caddy docs) so that failed attempts do not use up the real rate limit.

## Update the server

```bash
sudo /opt/sunsync/deploy/update.sh
```

It fast-forwards to `origin/main`, installs the packages, builds the web app, copies it to `/var/www/sunsync` and restarts the API and the measurement server. Do not deploy on the day before a demo (code freeze: 11/19 and 12/10).

## Automatic update

A timer can deploy `main` for you. Every 15 minutes it fetches `main`; when there is a commit that is not deployed yet, it takes a database backup and runs `update.sh`. Nothing happens unless the switch file `/etc/sunsync/autoupdate` exists, so it stays off until you turn it on.

Turn it on (once):

```bash
sudo cp /opt/sunsync/deploy/sunsync-autoupdate.service /opt/sunsync/deploy/sunsync-autoupdate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo touch /etc/sunsync/autoupdate
sudo systemctl enable --now sunsync-autoupdate.timer
```

- **Freeze** (before a demo; code freeze on 11/19 and 12/10): `sudo rm /etc/sunsync/autoupdate`. To resume: `sudo touch /etc/sunsync/autoupdate`.
- **Check:** `systemctl list-timers 'sunsync-*'`, `sudo journalctl -u sunsync-autoupdate -n 30 --no-pager`, and `cat /var/lib/sunsync/deployed-commit` for the commit that runs now.
- **When a deployment fails:** the running services are not restarted, the commit is written to `/var/lib/sunsync/failed-commit` and it is not tried again. Fix the cause and then either merge a new commit or remove that file to try the same commit again.
- A merged pull request is deployed within about 15 minutes, so merge only when the checks pass.
- Turn the switch off while you try another branch on the server; otherwise the server is switched back to `main`.
- `/opt/sunsync` on the server must have no local changes.

## Check that it works

| Check | How |
|---|---|
| Services are running | `systemctl status sunsync-api sunsync-measure` |
| Timers are set | `systemctl list-timers 'sunsync-*'` |
| Landing page is public | Open `https://YOUR_NAME.duckdns.org` in a private window: the page opens without a password |
| Data is locked | `curl -i https://YOUR_NAME.duckdns.org/api/health` returns 401 |
| API answers | `curl -u USER:PASSWORD https://YOUR_NAME.duckdns.org/api/health` |
| Measurement server answers | `curl -u USER:PASSWORD https://YOUR_NAME.duckdns.org/measure/health` |
| Forecast worker works | `sudo systemctl start sunsync-worker.service && journalctl -u sunsync-worker -n 5` |
| Backup works | `sudo systemctl start sunsync-backup.service && ls /var/lib/sunsync/backups` |
| Survives a reboot | `sudo reboot`, then open the address again |
| Phone, direct | Open the address on a phone and use the app from creating a space to the measurement result |
| Phone, QR | Create a space on the PC, press "Capture on phone" and scan the QR code |

## Before a demo

- **Print the 4-marker board at its real size** (four markers, IDs 7 to 10, 24 cm markers with 4 cm gaps as the team currently uses; that is about 52 cm on each side, larger than A3, so confirm the size with whoever prints it) and stick it on the window. The measurement server accepts only this board.
- **Prepare each phone camera once** in the app under "더보기" (about 12 photos of the board). The result is saved in that phone's browser for this address only, so it has to be repeated when the address changes, in another browser, in the installed app if it was done in the browser, or after the browser data is cleared.
- Allow the camera when the browser asks.
- Do the whole flow once on every phone that will be used, with the real address, a day before the demo (code freeze: 11/19 and 12/10).

## How a teammate checks the deployment

- **Needs nothing:** read the pull request, the code and the docs, look at the CI results, and open the landing page.
- **Needs the temporary user name and password:** trying the app yourself, because every request for data (`/api`, `/measure`) is locked until the Google login ships. Ask the person who runs the server for the address and the password. Never write them in the repository, an issue or a pull request: the repository is public. The server key file (`.pem`) is never shared; someone who has to log in to the server sends the public part of their own SSH key instead (`id_ed25519.pub`), and the server owner adds it.

Steps (use a private window on a PC and a phone):

1. Open the address. The landing page opens without a password.
2. Press "내 공간 열기". The browser asks for the user name and password once; after that the app shows its own account screen (not a real login yet, any name works).
3. Create a space with the window size and the building location.
4. Press "휴대폰에서 촬영". A QR code appears; scan it with a phone and the app opens on the capture screen.
5. On the phone, open the address directly: the app layout appears and the camera asks for permission.
6. The camera setup and the board capture need the printed 4-marker board (see "Before a demo").

Everyone who has the password sees the same spaces until the spaces are separated by account, so do not enter personal data.

## Backup and logs

- A daily timer copies the database to `/var/lib/sunsync/backups` and keeps the 14 newest copies. The copy uses SQLite's own backup, because the database runs in WAL mode and a plain file copy can be broken. Download a copy now and then with `scp`; a backup on the same disk does not survive losing the server.
- Logs: `journalctl -u sunsync-api -u sunsync-measure -u caddy`.

## Notes

- **Photos are not stored.** The measurement server receives each photo as base64 inside the request, uses it in memory and returns only the detected markers; no code writes it to disk. Requests are limited to 16 MB by the server and 20 MB by Caddy.
- **Forecast worker.** The API works without it (it falls back to the clear-sky value), but the forecast comparison needs it. It runs once an hour, only for the building locations saved in spaces, and calls Open-Meteo, so the server needs outbound internet access.
- **Basic auth** in the `Caddyfile` locks only the data (`/api` and `/measure`) until the Google login ships. The web app files stay public, so the landing page opens without a password and the phone can load the manifest and install the app. The browser asks for the password when the app first requests data; whether the app handles that prompt cleanly has to be checked on the server. If it does not, move `import locked` to the whole site block.
- **Remove the server after the last demo (12/11)**: delete the instance and the public IP in the AWS console and download a last backup first. Set an AWS budget alert when you create the account.

## Not verified yet

These were written without a real server and have to be confirmed on it: the certificate for the free address, the server size (memory with OpenCV and pvlib together), that the OpenCV libraries are enough, and the phone camera over https.
