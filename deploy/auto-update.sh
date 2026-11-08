#!/usr/bin/env bash
# Deploys main when it has new commits. Started every 15 minutes by sunsync-autoupdate.timer as root.
# It does nothing unless the switch file exists: remove /etc/sunsync/autoupdate to freeze the server.
set -euo pipefail

ROOT=${SUNSYNC_ROOT:-/opt/sunsync}
STATE=${SUNSYNC_STATE_DIR:-/var/lib/sunsync}
SWITCH=${SUNSYNC_AUTOUPDATE_SWITCH:-/etc/sunsync/autoupdate}
UPDATE=${SUNSYNC_UPDATE_SCRIPT:-$ROOT/deploy/update.sh}

[ -f "$SWITCH" ] || exit 0

cd "$ROOT"
git fetch --quiet origin main
TARGET=$(git rev-parse origin/main)

# Skip a commit that is already deployed, and a commit whose deployment failed.
[ "$TARGET" = "$(cat "$STATE/deployed-commit" 2>/dev/null || true)" ] && exit 0
[ "$TARGET" = "$(cat "$STATE/failed-commit" 2>/dev/null || true)" ] && exit 0

echo "Deploying $TARGET"
# The backup keeps the 14 newest copies, so it is taken only when a deployment really starts.
systemctl start sunsync-backup.service
if "$UPDATE"; then
  rm -f "$STATE/failed-commit"
else
  echo "$TARGET" > "$STATE/failed-commit"
  echo "Deployment of $TARGET failed; it is not tried again until main changes" >&2
  exit 1
fi
