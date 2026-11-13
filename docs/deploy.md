# Deployment on one server

SunSync runs on one Linux server without Docker. Caddy serves the web app over https and forwards `/api` to the API and `/measure` to the measurement server. The files are in `deploy/`.

```
phone / PC --https--> Caddy (443)
                       |- /api/*      -> API (uvicorn, 127.0.0.1:8000) -- /var/lib/sunsync/sunsync.db
                       |- /measure/*  -> measurement server (uvicorn, 127.0.0.1:8001)
                       '- everything else -> built web app in /var/www/sunsync
  every hour: forecast worker; every 10 minutes: KMA worker, AI nowcast worker --> same database file
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
   sudo git clone https://github.com/hing9-9-9/SunSync.git /opt/sunsync
   sudo python3 -m venv /opt/sunsync/.venv
   ```

4. **Settings.** Copy `deploy/env.example` to `/etc/sunsync/sunsync.env`, replace `YOUR_NAME`, and `chmod 600` it.

5. **Services.**

   ```bash
   sudo cp /opt/sunsync/deploy/*.service /opt/sunsync/deploy/*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable sunsync-api sunsync-measure sunsync-worker.timer sunsync-kma-worker.timer sunsync-ai-nowcast-worker.timer sunsync-backup.timer
   sudo /opt/sunsync/deploy/update.sh      # installs, builds the web app and starts the services
   sudo systemctl start sunsync-worker.timer sunsync-kma-worker.timer sunsync-ai-nowcast-worker.timer sunsync-backup.timer
   ```

6. **Address and https.** Create a free name at https://www.duckdns.org and point it to the server's public IP. Open ports 80 and 443 (and 22 only for your own IP) in the AWS security group; Caddy needs 80 and 443 to get the certificate. Then:

   ```bash
   sudo cp /opt/sunsync/deploy/Caddyfile /etc/caddy/Caddyfile
   sudo nano /etc/caddy/Caddyfile           # set YOUR_NAME
   caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

   The template uses the Google login: the API checks the login itself and Caddy asks `/api/auth/check` for every `/measure` request (`import login`). Switch the login on first ([auth.md](auth.md)); `/api/auth/check` answers 401 while the login is off, so `/measure` stays closed until then. If the login is not ready yet, the commented `locked` block of the template is a temporary lock with a user name and password: run `caddy hash-password`, put the hash in, and add `import locked` to the `/api/*` and `/measure/*` blocks.

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
- Turn the switch off while you try another branch on the server (`sudo env SUNSYNC_BRANCH=<branch> /opt/sunsync/deploy/update.sh`); otherwise the server is switched back to `main`.
- `/opt/sunsync` on the server must have no local changes.
- **The update runs the code of the repository as root** (`update.sh`, `pip install`, `npm ci`). Whoever can merge into `main` controls the server. Keep the branch protection of `main` (required checks and a review) and the list of people with write access short.
- The web build gets only `VITE_PUBLIC_URL` from `/etc/sunsync/sunsync.env` (a line `VITE_PUBLIC_URL=https://...` without quotes), so the Google secret is not visible to the packages that are installed there.

## Check that it works

| Check | How |
|---|---|
| Services are running | `systemctl status sunsync-api sunsync-measure` |
| Timers are set | `systemctl list-timers 'sunsync-*'` |
| Landing page is public | Open `https://YOUR_NAME.duckdns.org` in a private window: the page opens without a login |
| Data is locked | `curl -i https://YOUR_NAME.duckdns.org/api/ui/spaces` returns 401 |
| API answers | `curl -i https://YOUR_NAME.duckdns.org/api/health` returns 200 (the health check is open) |
| Measurement server is locked | `curl -i https://YOUR_NAME.duckdns.org/measure/health` returns 401; with a login it is reached through the app |
| Login works | In a private window go to "내 공간 열기", log in with a test-user account, and come back to the page ([auth.md](auth.md)) |
| Forecast workers work | Run `sunsync-worker.service`, `sunsync-kma-worker.service`, then `sunsync-ai-nowcast-worker.service`; inspect each journal |
| Backup works | `sudo systemctl start sunsync-backup.service && ls /var/lib/sunsync/backups` |
| Survives a reboot | `sudo reboot`, then open the address again |
| Phone, direct | Open the address on a phone, log in, and use the app from creating a space to the measurement result (taking photos needs the native app, see "Before a demo") |
| Phone, QR | Create a space on the PC, press "Capture on phone" and scan the QR code |

## Before a demo

- **Use the single 60 cm marker** (a black outer square of 0.6 m x 0.6 m, 6 x 6 cells of 10 cm; [fr03_single_marker.md](fr03_single_marker.md)). Nothing else is printed. A marker that is printed or shown on a screen must have the size and the ID that the capture session reports, and the measurement server stops when the saved marker size is not 0.6 m. The film display is virtual in this term.
- **Prepare each phone camera once.** The app prepares the camera from 12 to 30 different views of the whole marker. The result is saved on that phone for this address only, so it has to be repeated when the address changes, in another browser, or after the browser data is cleared.
- **Taking photos in the browser is a development test and is off in production builds** ([README](../README.md)); a demo capture needs the native app ([mobile/README.md](../mobile/README.md)).
- **Not verified on the server yet:** the capture with the single marker and the camera preparation (issue #112).
- Allow the camera when the browser asks.
- Do the whole flow once on every phone that will be used, with the real address, a day before the demo (code freeze: 11/19 and 12/10).

## How a teammate checks the deployment

- **Needs nothing:** read the pull request, the code and the docs, look at the CI results, and open the landing page.
- **Needs a Google account that is a test user of the app:** trying the app yourself, because every request for data (`/api`, `/measure`) needs the Google login. Send the e-mail address of your Google account to the person who runs the server; they add it under Test users in the Google console ([auth.md](auth.md)), and an account that is not on the list is refused. Never write passwords, keys or secrets in the repository, an issue or a pull request: the repository is public. The server key file (`.pem`) is never shared; someone who has to log in to the server sends the public part of their own SSH key instead (`id_ed25519.pub`), and the server owner adds it.

Steps (use a private window on a PC and a phone):

1. Open the address. The landing page opens without a login.
2. Press "내 공간 열기". The app sends you to Google; log in with the account that was added as a test user, and you come back to the page.
3. Create a space with the window size and the building location.
4. Press "휴대폰에서 촬영". A QR code appears; scan it with a phone and the app opens on the capture screen. The phone logs in on its own, so log in there with the same account.
5. On the phone, open the address directly: the app layout appears and the camera asks for permission.
6. The camera setup and the capture need the 60 cm marker (see "Before a demo").

Each account sees only its own spaces ([auth.md](auth.md)); spaces made before the accounts existed are visible to nobody until they are given to an account.

## When an old screen appears

The web app is an installable page with a service worker (`/sw.js`, generated at build time). The worker keeps only the cache of the current build, takes over at once on a new build, and always asks the server first for the page, `manifest.webmanifest` and `/design/*`; the cache is used only when the server cannot be reached. A page that stays open shows a notice ("새 버전이 있어요") when a new build is ready. The account screen shows the running build as "버전 <commit>"; compare it with `cat /var/lib/sunsync/deployed-commit` on the server (first 7 characters).

Check in this order:

1. Server: `cat /var/lib/sunsync/deployed-commit` is the newest commit, `/var/lib/sunsync/failed-commit` does not exist, and `journalctl -u sunsync-autoupdate -n 50` shows no error. Delete `failed-commit` after the cause is fixed so the next run tries again.
2. Server: the live Caddyfile is not updated by the deployment. Compare it with `deploy/Caddyfile` (`diff /etc/caddy/Caddyfile /opt/sunsync/deploy/Caddyfile`, only YOUR_NAME may differ). The page, `/sw.js` and `/index.html` must answer `Cache-Control: no-store, no-cache, must-revalidate`; `/manifest.webmanifest`, `/design/*` and `/icons/*` answer `no-cache`. After editing: `caddy validate --config /etc/caddy/Caddyfile`, then `sudo systemctl reload caddy`.
3. Browser (F12, Application): Service Workers shows one active worker and Cache Storage holds one `sunsync-ui-<commit>` entry. Several entries mean a device still runs an old worker: use "Unregister" and "Clear site data" once, or close and re-add the installed app. Network with "Disable cache" off shows whether a file came from `(ServiceWorker)` or `(disk cache)`.

Unit files: `update.sh` runs `deploy/install-units.sh`, which copies new or changed `deploy/*.service` and `*.timer` to `/etc/systemd/system/`, runs `daemon-reload`, and enables only timers that were not installed before. `sunsync-autoupdate.timer` stays under the switch file `/etc/sunsync/autoupdate`.

## Checking the weather collection

Three timers collect weather for every saved building location: `sunsync-worker` (Open-Meteo, every hour), `sunsync-kma-worker` and `sunsync-ai-nowcast-worker` (every 10 minutes, they need `KMA_API_KEY` in `/etc/sunsync/sunsync.env`). Each run writes one line to the journal:

```
sites=11 stored=11 failed=0 lease_acquired=1 pruned_snapshots=0 pruned_runs=0
sites=11 stored=5 failed=6 lease_acquired=1 reasons=kma_transport_error:6
```

- `lease_acquired=0` means another run was still holding the lock; nothing is wrong.
- `reasons` names why sites failed, with fixed words and counts only (never a response, an address or a key). Open-Meteo: `open_meteo_timeout`, `open_meteo_transport_error`, `open_meteo_invalid_response`. KMA: `kma_transport_error`, or a `kma_invalid_*` / `kma_empty_response` word when the answer cannot be used (the full list is in `backend/app/nowcast/kma_adapter.py`).
- **Retry and time limits.** Open-Meteo: 5 seconds per request and one more try after 2 seconds for a network failure (not for a bad response). KMA: 90 seconds per request and three tries with a growing pause.
- **When a run counts as failed.** The service is marked failed only when it tried and stored nothing. A run that stored some sites and failed on others (for example a site the provider cannot serve) is reported in the line but stays successful, so `systemctl --failed` shows real outages. There is no e-mail or message alert: look at `systemctl --failed` or the lines below.

Look at the recent runs and the last collection (the server has no `sqlite3` program, so use the virtualenv's Python):

```bash
journalctl -u sunsync-worker -u sunsync-kma-worker --since "-3h" --no-pager | grep sites=
sudo -u sunsync /opt/sunsync/.venv/bin/python - <<'PY'
import sqlite3
c = sqlite3.connect("file:/var/lib/sunsync/sunsync.db?mode=ro", uri=True)
for row in c.execute("select site_key, max(fetched_at), count(*) from weather_snapshot "
                     "where provider='open_meteo' group by site_key order by 2"): print(row)
print(c.execute("select count(*), coalesce(sum(p.fallback_reason is not null), 0) from nowcast_point p "
                "join nowcast_run r using(run_id) where r.created_at > datetime('now','-1 day')").fetchone())
PY
```

The first block lists each site with the time of its last Open-Meteo snapshot and how many snapshots it has; a site that is hours behind is failing. The last line is the number of forecast points of the last 24 hours and how many of them used the clear-sky fallback.

- **Old data is deleted by the Open-Meteo worker.** Every hour adds one snapshot per site (168 hourly points each). After each collection the worker deletes snapshots older than `SUNSYNC_SNAPSHOT_KEEP_DAYS` days (default 14; set it in `/etc/sunsync/sunsync.env`, for example 45 to keep everything until the last demo) and forecast runs older than 30 days or older than the setting, whichever is longer (so 45 keeps runs for 45 days; a snapshot that an unexpired run still uses is kept), and adds `pruned_snapshots=N pruned_runs=M` to its log line (`prune=failed` if the cleanup failed, `prune=skipped_bad_setting` and nothing deleted if the setting is not a whole number of days; the collection itself is not marked failed). With the default, the first deletions happen two weeks after the first collection. Analyses that replay past weather (for example the FR-04 comparison on real days) need the history: copy a database backup before the deletions start or raise the setting. The tables of the AI nowcast (`ai_nowcast_*`) are not cleaned by this.
- **Test spaces count too.** Every saved space with its own location is collected. On the demo server the spaces placed outside Korea were reported as failed KMA sites in every run (presumably because the provider does not cover them; the `reasons` word shows the actual cause).
- The forecast chart of a space states which source it shows (KMA-based AI, general forecast, or a fallback) under the chart.

## Backup and logs

The database and its copies hold e-mail addresses once the Google login is on, so they must not be readable by other users. The services now create their files private (`UMask=0077`; copy the unit files to `/etc/systemd/system/` and run `sudo systemctl daemon-reload` to apply this) and `backup.sh` writes private copies. One time, for the files that already exist: `sudo chmod -R go-rwx /var/lib/sunsync` (the owner keeps access, so the services keep working).

- A daily timer copies the database to `/var/lib/sunsync/backups` and keeps the 14 newest copies. The copy uses SQLite's own backup, because the database runs in WAL mode and a plain file copy can be broken. Download a copy now and then with `scp`; a backup on the same disk does not survive losing the server.
- Logs: `journalctl -u sunsync-api -u sunsync-measure -u caddy`.

## Notes

- **Photos are not stored.** The measurement server receives each photo as base64 inside the request, uses it in memory and returns only the detected markers; no code writes it to disk. Requests are limited to 16 MB by the server and 20 MB by Caddy.
- **Forecast workers.** The API works without them (it falls back to the clear-sky value), but the forecast comparison needs them. `sunsync-worker` runs once an hour, only for the building locations saved in spaces, and calls Open-Meteo. `sunsync-kma-worker` and then `sunsync-ai-nowcast-worker` run every 10 minutes for the AI nowcast; they need `KMA_API_KEY` and `FR01_AI_ROLLOUT_MODE` (`shadow` until the team has reviewed `active`) in `/etc/sunsync/sunsync.env`. All of them call external services, so the server needs outbound internet access.
- **Login and locks.** With the Google login on, the API refuses every request without a session (except the health check and the login routes), and Caddy asks `/api/auth/check` for every `/measure` request. The web app files stay public, so the landing page opens and the phone can load the manifest and install the app. The basic auth block (`locked`) in the template is only a temporary lock for a server whose login is not on yet.
- **Remove the server after the last demo (12/11)**: delete the instance and the public IP in the AWS console and download a last backup first. Set an AWS budget alert when you create the account.

## Verified and not verified

Confirmed on the server (the checked boxes of issues #104, #105 and #110): the https address with its certificate, direct use on a phone, the QR code, services that restart after a reboot, the Google login round trip, logout, removal of the temporary basic auth, `/measure` closed without a login, and the first automatic deployment with its backup.

Not verified on the server yet:

- the end of a session after 30 minutes without use and after 12 hours (#105: only checked with a controlled clock in the tests);
- stopping the automatic update by removing the switch file, and what happens when a deployment fails (#110: only checked in the tests);
- that a capture taken on the phone appears in the plan view on the PC (#104);
- the capture with the single marker and the camera preparation (#112);
- the memory of the server with OpenCV and pvlib at work: no problem has been reported, but it was not measured.
