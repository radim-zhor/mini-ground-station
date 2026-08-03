#!/bin/bash
#
# Weekly backup of the ground-station database (Neon) to a local dump.
#
# This file is the versioned source. launchd runs a COPY of it from
# ~/.local/bin/gs-backup.sh, because the repo lives in ~/Documents and macOS
# TCC denies launchd-spawned processes access there (same reason the agent's
# own launchd job fails, see docs/bezobsluzny-provoz.md). After editing this
# file, re-run the install step in docs/migrace-neon.md.
#
# Reads DATABASE_URL from ~/.config/ground-station/db.env (chmod 600) so the
# password is never on a command line or in this script.
#
set -euo pipefail

ENV_FILE="${HOME}/.config/ground-station/db.env"
BACKUP_DIR="${HOME}/gs-backups"
LOG_FILE="${BACKUP_DIR}/backup.log"
KEEP=8                       # weekly dumps to retain (~2 months)
MIN_BYTES=5000               # a dump smaller than this cannot hold real data
PG_DUMP="/opt/homebrew/opt/libpq/bin/pg_dump"
PG_RESTORE="/opt/homebrew/opt/libpq/bin/pg_restore"

mkdir -p "${BACKUP_DIR}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LOG_FILE}"; }

# Best-effort push notification, same channel the agent already uses.
notify() {
    [ -n "${NTFY_TOPIC:-}" ] || return 0
    curl -s -m 10 -H "Title: Ground station backup" -d "$1" \
        "https://ntfy.sh/${NTFY_TOPIC}" >/dev/null 2>&1 || true
}

fail() {
    log "FAIL: $*"
    notify "Záloha databáze selhala: $*"
    exit 1
}

[ -r "${ENV_FILE}" ] || fail "chybí ${ENV_FILE}"
# shellcheck source=/dev/null
. "${ENV_FILE}"
[ -n "${DATABASE_URL:-}" ] || fail "${ENV_FILE} nedefinuje DATABASE_URL"
[ -x "${PG_DUMP}" ] || fail "chybí ${PG_DUMP} (brew install libpq)"

OUT="${BACKUP_DIR}/gs-$(date +%F).dump"
TMP="${OUT}.partial"

# Dump to a .partial name and only rename once it passes verification, so a
# half-written file can never be mistaken for a usable backup.
log "start"
"${PG_DUMP}" "${DATABASE_URL}" -Fc -f "${TMP}" 2>>"${LOG_FILE}" \
    || { rm -f "${TMP}"; fail "pg_dump skončil chybou"; }

SIZE=$(wc -c <"${TMP}" | tr -d ' ')
[ "${SIZE}" -ge "${MIN_BYTES}" ] || { rm -f "${TMP}"; fail "dump má jen ${SIZE} B"; }

# An empty database produces a perfectly valid dump of the right size, and
# pg_restore -l lists TABLE DATA even for a table with no rows — measured, a
# dump of an empty but migrated DB was 6710 B and passed both those checks.
# So count the actual COPY payload: that is the only thing that proves the
# backup is worth keeping.
ROWS=$("${PG_RESTORE}" --data-only -t contacts -f - "${TMP}" 2>/dev/null | awk '
    /^COPY public\.contacts /  { in_copy = 1; next }
    in_copy && /^\\\.$/        { in_copy = 0 }
    in_copy                    { n++ }
    END                        { print n + 0 }')
[ "${ROWS}" -gt 0 ] || { rm -f "${TMP}"; fail "dump neobsahuje ani jeden kontakt"; }

mv -f "${TMP}" "${OUT}"
log "OK ${OUT} (${SIZE} B, ${ROWS} kontaktů)"

# Rotation: keep the newest $KEEP dumps, drop the rest.
REMOVED=0
while IFS= read -r old; do
    rm -f "${old}"
    REMOVED=$((REMOVED + 1))
done < <(ls -t "${BACKUP_DIR}"/gs-*.dump 2>/dev/null | tail -n "+$((KEEP + 1))")
[ "${REMOVED}" -gt 0 ] && log "rotace: smazáno ${REMOVED} starých záloh"

exit 0
