#!/usr/bin/env bash
# One-shot host preparation for a fresh Ubuntu LTS VPS (T009).
#
# Idempotent: safe to re-run. It installs the clock and firewall pieces, creates the
# service account, and installs the systemd units. It never writes a credential: those are
# rendered later from the encrypted files by atlas-render-credentials.service.
set -euo pipefail

readonly ATLAS_HOME="${ATLAS_HOME:-/opt/atlas}"
readonly REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

log() { printf '%s %s\n' "[bootstrap]" "$*"; }
die() { printf '%s %s\n' "[bootstrap] ERROR:" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"

log "installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq chrony ufw fail2ban unattended-upgrades python3-venv rsync

# --- clock ------------------------------------------------------------------------------
# The daily loss boundary is 00:00 WIB, and a clock that drifts still corrupts bar
# timestamps and TOTP codes. makestep matters: a VM resumed from a snapshot jumps, and slew
# alone would take hours to catch up.
log "configuring chrony"
cat >/etc/chrony/chrony.conf <<'CHRONY'
pool id.pool.ntp.org iburst
makestep 1.0 3
rtcsync
driftfile /var/lib/chrony/chrony.drift
logdir /var/log/chrony
CHRONY
systemctl enable chrony
systemctl restart chrony

# --- firewall ---------------------------------------------------------------------------
# Default deny. SSH stays open; everything else arrives over Tailscale, so the public
# interface exposes no application port at all.
log "configuring ufw"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'ssh'
ufw allow in on tailscale0 comment 'tailnet: gateway and operator access'
ufw --force enable

# --- service account and directories ----------------------------------------------------
log "creating service account"
id -u atlas >/dev/null 2>&1 || useradd --system --create-home --home-dir "$ATLAS_HOME" \
  --shell /usr/sbin/nologin atlas
install -d -m 0755 -o atlas -g atlas "$ATLAS_HOME"
install -d -m 0700 -o root -g root /run/atlas
install -d -m 0700 -o root -g root /etc/atlas

# --- application ------------------------------------------------------------------------
log "installing application into ${ATLAS_HOME}"
if [[ -d "$REPO_DIR/services" ]]; then
  rsync -a --exclude '.git' --exclude '.venv' "$REPO_DIR/" "$ATLAS_HOME/"
fi
if [[ ! -x "$ATLAS_HOME/.venv/bin/python" ]]; then
  python3 -m venv "$ATLAS_HOME/.venv"
  "$ATLAS_HOME/.venv/bin/pip" install --quiet --upgrade pip
fi
"$ATLAS_HOME/.venv/bin/pip" install --quiet \
  "$ATLAS_HOME/packages/schemas" "$ATLAS_HOME/packages/common"
chown -R atlas:atlas "$ATLAS_HOME"

# --- systemd -----------------------------------------------------------------------------
log "installing systemd units"
install -m 0644 "$ATLAS_HOME/infra/vps/systemd/atlas.target" /etc/systemd/system/
install -m 0644 "$ATLAS_HOME/infra/vps/systemd/atlas@.service" /etc/systemd/system/
install -m 0644 "$ATLAS_HOME/infra/vps/systemd/atlas-render-credentials.service" \
  /etc/systemd/system/
install -m 0644 "$ATLAS_HOME/infra/vps/systemd/atlas-backup.service" /etc/systemd/system/
install -m 0644 "$ATLAS_HOME/infra/vps/systemd/atlas-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable atlas-render-credentials.service
# Nightly pg_dump; ATLAS_BACKUP_COPY in /run/atlas/backup.env decides whether it leaves the host.
systemctl enable --now atlas-backup.timer
# atlas.target is enabled per service once that package exists (M1+).

log "done. Next: place the age key at /etc/atlas/age.key (0600), encrypt the credential"
log "files, then 'systemctl start atlas.target'. Verify the clock with: chronyc tracking"
