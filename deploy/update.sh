#!/usr/bin/env bash
# Deploys the latest main on the server. Run as root: sudo /opt/sunsync/deploy/update.sh
# To try another branch before it is merged: sudo env SUNSYNC_BRANCH=feat/deploy-aws /opt/sunsync/deploy/update.sh
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

# The address in the QR code is fixed when the web app is built. Only this one setting is given to
# the build: the packages that are installed there must not see the Google secret.
VITE_PUBLIC_URL=$(sed -n 's/^VITE_PUBLIC_URL=//p' /etc/sunsync/sunsync.env | tail -n 1)
export VITE_PUBLIC_URL
(cd frontend && npm ci && npm run build)
mkdir -p "$WEB"
rsync -a --delete frontend/dist/ "$WEB"/

systemctl restart sunsync-api sunsync-measure
# auto-update.sh compares main with this commit to see whether anything is new.
git rev-parse HEAD > "$STATE/deployed-commit"
echo "Deployed $(git rev-parse --short HEAD)"
