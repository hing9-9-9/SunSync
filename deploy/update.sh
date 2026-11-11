#!/usr/bin/env bash
# Deploys the latest main on the server. Run as root: sudo /opt/sunsync/deploy/update.sh
# To try another branch before it is merged: sudo env SUNSYNC_BRANCH=<branch> /opt/sunsync/deploy/update.sh
set -euo pipefail

ROOT=/opt/sunsync
WEB=/var/www/sunsync
STATE=${SUNSYNC_STATE_DIR:-/var/lib/sunsync}
BRANCH=${SUNSYNC_BRANCH:-main}

cd "$ROOT"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git merge --ff-only "origin/$BRANCH"

"$ROOT/.venv/bin/pip" install --quiet -r requirements.txt -e "measure/registration-core[api]"

# Install new or changed systemd units before they are restarted below (a timer that was added to
# deploy/ after the server was set up would otherwise stop this script).
"$ROOT/deploy/install-units.sh"

# The address in the QR code is fixed when the web app is built. Only this one setting is given to
# the build: the packages that are installed there must not see the Google secret.
VITE_PUBLIC_URL=$(sed -n 's/^VITE_PUBLIC_URL=//p' /etc/sunsync/sunsync.env | tail -n 1)
export VITE_PUBLIC_URL
# The commit that is built is shown on the account screens, so a person can tell which version runs.
VITE_BUILD_ID=$(git rev-parse --short HEAD)
export VITE_BUILD_ID
(cd frontend && npm ci && npm run build)
mkdir -p "$WEB"
rsync -a --delete frontend/dist/ "$WEB"/

systemctl restart sunsync-api sunsync-measure
systemctl restart sunsync-worker.timer sunsync-kma-worker.timer sunsync-ai-nowcast-worker.timer
# auto-update.sh compares main with this commit to see whether anything is new.
git rev-parse HEAD > "$STATE/deployed-commit"
echo "Deployed $(git rev-parse --short HEAD)"
