#!/usr/bin/env bash
# Nightly logical backup, kept off the application disk (T009).
#
# `pg_dump` in custom format so a single object can be restored with pg_restore -t.
# The dump is written locally first (fast, no network dependency for the backup itself),
# then copied off-box with the command in ATLAS_BACKUP_COPY. If the copy fails, the script
# exits non-zero so the systemd unit reports failed and the alert fires: a backup that only
# exists on the machine it protects is not a backup.
set -euo pipefail

readonly DEST_DIR="${ATLAS_BACKUP_DIR:-/var/lib/atlas-backup}"
readonly RETENTION_DAYS="${ATLAS_BACKUP_RETENTION_DAYS:-14}"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly DUMP="${DEST_DIR}/atlas-${STAMP}.dump"

log() { printf '%s %s\n' "[backup]" "$*"; }
die() { printf '%s %s\n' "[backup] ERROR:" "$*" >&2; exit 1; }

[[ -n "${ATLAS_DB_DSN:-}" ]] || die "ATLAS_DB_DSN is not set"
command -v pg_dump >/dev/null || die "pg_dump is not installed"

# The compose stack and Atlas itself speak SQLAlchemy URLs (`postgresql+asyncpg://`),
# and libpq rejects the driver suffix, so strip it before handing the DSN to pg_dump.
readonly DSN="$(printf '%s' "$ATLAS_DB_DSN" | sed -E 's#\+[A-Za-z0-9_]+://#://#')"

install -d -m 0700 "$DEST_DIR"

log "dumping to ${DUMP}"
# --no-owner/--no-privileges: the restore target may map roles differently.
pg_dump --format=custom --compress=zstd --no-owner --no-privileges \
  --file="$DUMP" "$DSN"

log "verifying the archive headers"
pg_restore --list "$DUMP" >/dev/null || die "dump is unreadable: ${DUMP}"
chmod 0600 "$DUMP"

if [[ -n "${ATLAS_BACKUP_COPY:-}" ]]; then
  log "copying off-box"
  # shellcheck disable=SC2086 # the copy command is intentionally a command line
  eval ${ATLAS_BACKUP_COPY} "$DUMP" || die "off-box copy failed: ${DUMP} remains local only"
else
  log "ATLAS_BACKUP_COPY is unset: the dump exists on this host only"
fi

log "pruning local dumps older than ${RETENTION_DAYS} days"
find "$DEST_DIR" -maxdepth 1 -type f -name 'atlas-*.dump' \
  -mtime "+${RETENTION_DAYS}" -print -delete

log "done"
