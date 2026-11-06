#!/usr/bin/env bash
# Copies the live database consistently (it runs in WAL mode, so a plain file copy can be broken).
# Keeps the 14 newest copies. Run by sunsync-backup.timer as root.
set -euo pipefail

DB=${SUNSYNC_DB_FILE:-/var/lib/sunsync/sunsync.db}
DEST=${SUNSYNC_BACKUP_DIR:-/var/lib/sunsync/backups}

mkdir -p "$DEST"
TARGET="$DEST/sunsync-$(date -u +%Y%m%dT%H%M%SZ).db"
/opt/sunsync/.venv/bin/python - "$DB" "$TARGET" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
target.close()
source.close()
PY
ls -1t "$DEST"/sunsync-*.db | tail -n +15 | xargs -r rm --
echo "Backed up to $TARGET"
