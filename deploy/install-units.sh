#!/usr/bin/env bash
# Installs the systemd units of deploy/ on the server. update.sh runs it (as root) after every update.
#
# - A unit that is not on the server yet is installed; a new timer is enabled and started.
# - A unit that differs from deploy/ is replaced (deploy/ is the source of truth).
# - daemon-reload runs only when something changed.
# - The automatic update (sunsync-autoupdate.timer) is never enabled here: it is turned on by hand
#   together with its switch file (docs/deploy.md).
# SUNSYNC_UNIT_SRC and SUNSYNC_UNIT_DIR exist for the tests.
set -euo pipefail

SRC=${SUNSYNC_UNIT_SRC:-$(cd "$(dirname "$0")" && pwd)}
DEST=${SUNSYNC_UNIT_DIR:-/etc/systemd/system}

changed=0
new_timers=()
for file in "$SRC"/*.service "$SRC"/*.timer; do
  [ -e "$file" ] || continue
  name=$(basename "$file")
  if [ ! -e "$DEST/$name" ]; then
    install -m 644 "$file" "$DEST/$name"
    changed=1
    echo "Installed $name"
    case "$name" in
      sunsync-autoupdate.timer) ;;
      *.timer) new_timers+=("$name") ;;
    esac
  elif ! cmp -s "$file" "$DEST/$name"; then
    install -m 644 "$file" "$DEST/$name"
    changed=1
    echo "Updated $name"
  fi
done

if [ "$changed" = 1 ]; then
  systemctl daemon-reload
fi
for timer in ${new_timers[@]+"${new_timers[@]}"}; do
  systemctl enable --now "$timer"
done
