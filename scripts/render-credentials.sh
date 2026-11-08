#!/usr/bin/env bash
# Decrypt infra/age/*.sops.yaml into /run/atlas, then lock the results down.
#
# Runs as root from atlas.target's ExecStartPre. It fails loudly on purpose: a service that
# starts with half a secret set is worse than one that does not start.
set -euo pipefail

readonly TARGET_DIR="${TARGET_DIR:-/run/atlas}"
readonly SOURCE_DIR="${SOURCE_DIR:-$(dirname "$0")/../infra/age}"
readonly AGE_KEY="${AGE_KEY:-/etc/atlas/age.key}"

command -v sops >/dev/null || { echo "sops is not installed" >&2; exit 1; }
[[ -r "$AGE_KEY" ]] || { echo "age key ${AGE_KEY} is missing or unreadable" >&2; exit 1; }

install -d -m 0700 "$TARGET_DIR"

shopt -s nullglob
files=("$SOURCE_DIR"/*.sops.yaml)
if ((${#files[@]} == 0)); then
  echo "no encrypted secret files in ${SOURCE_DIR}" >&2
  exit 1
fi

for src in "${files[@]}"; do
  base="$(basename "$src" .sops.yaml)"
  tmp="$(mktemp "${TARGET_DIR}/.${base}.XXXXXX")"
  SOPS_AGE_KEY_FILE="$AGE_KEY" sops --decrypt --extract '["data"]' \
    --output-type dotenv "$src" >"$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "${TARGET_DIR}/${base}.env"
  echo "rendered ${TARGET_DIR}/${base}.env"
done
