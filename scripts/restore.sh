#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh: replace the contents of each data mount listed in the backup's
# MANIFEST with its archive. The services are stopped while their data is replaced, and whatever was running is
# started again afterwards.
#
#   scripts/restore.sh [--yes] DIR [SERVICE...]
#
# Without SERVICE, every service that has an archive in DIR is restored. The checksums are verified first, and it
# asks for confirmation (unless --yes) after listing what it will overwrite. It only writes to a container path
# the service still mounts read-write, never to the Docker socket or a path the service excludes from backups. On a new host, restore .env from DIR/env first;
# the volumes and containers are created as needed. Uses `docker compose` from the checkout, so the standard
# Compose variables select another project, as in scripts/backup.sh.
set -euo pipefail

usage() {
  echo "usage: $0 [--yes] DIR [SERVICE...]" >&2
  exit 2
}

fail() {
  echo "error: $*" >&2
  exit 1
}

yes=false
if [ "${1:-}" = --yes ]; then
  yes=true
  shift
fi
[ $# -ge 1 ] || usage
backup=$(cd "$1" && pwd)
shift

root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=scripts/lib.sh
. "$root/scripts/lib.sh"
cd "$root"

[ -f "$backup/MANIFEST" ] && [ -f "$backup/SHA256SUMS" ] || fail "$backup has no MANIFEST or SHA256SUMS"
echo "Verifying $backup/SHA256SUMS"
(cd "$backup" && sha256 -c --quiet SHA256SUMS)
grep -q ' MANIFEST$' "$backup/SHA256SUMS" || fail "SHA256SUMS doesn't cover the MANIFEST"

# The archives to restore, from the MANIFEST, for the services asked for (or all). Its names are checked before
# they're used: an archive is a plain file in DIR, a mount an absolute container path.
known=" $(docker compose config --services | tr '\n' ' ') "
entries=()
while IFS=$'\t' read -r archive service mount _; do
  case $archive in '#'* | '') continue ;; esac
  if [[ ! "$archive" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*\.tar\.gz$ ]] || [ ! -f "$backup/$archive" ]; then
    fail "the MANIFEST names an archive that isn't in $backup: $archive"
  fi
  if [[ ! "$mount" =~ ^/[A-Za-z0-9_.@/-]+$ ]] || [[ "$mount" == *..* || "$mount" == *docker.sock ]]; then
    fail "$archive has an unusable mount path: $mount"
  fi
  if [[ "$known" != *" $service "* ]]; then
    fail "$archive belongs to $service, which isn't in compose.yaml"
  fi
  if [ $# -eq 0 ] || [[ " $* " == *" $service "* ]]; then entries+=("$archive	$service	$mount"); fi
done <"$backup/MANIFEST"
if [ ${#entries[@]} -eq 0 ]; then
  fail "nothing to restore in $backup${*:+ for $*}"
fi
services=$(printf '%s\n' "${entries[@]}" | cut -f2 | sort -u)

# Containers (and their volumes) must exist to write into; this creates missing ones without starting them.
# shellcheck disable=SC2086 # one service name per word
docker compose create --no-recreate $services

echo "This replaces everything in:"
for entry in "${entries[@]}"; do
  IFS=$'\t' read -r archive service mount <<<"$entry"
  id=$(docker compose ps --all --quiet "$service")
  if skipped_mounts "$id" | grep -qxF -- "$mount"; then
    fail "$service excludes $mount from backups ($skip_label); refusing to write it"
  fi
  source=$(data_mounts "$id" | awk -F '\t' -v m="$mount" '$1 == m { print $2 }')
  [ -n "$source" ] || fail "$service has no read-write mount at $mount any more (compose.yaml changed since the backup)"
  echo "  $service $mount: $source"
done
if ! $yes; then
  read -r -p "Type 'yes' to continue: " answer
  [ "$answer" = yes ] || {
    echo "Nothing changed."
    exit 1
  }
fi

running=$(docker compose ps --services --status running)
restart() {
  status=$?
  if [ -n "$running" ]; then
    echo "Starting what was running"
    # shellcheck disable=SC2086 # one service name per word
    docker compose start $running || status=1
  fi
  exit "$status"
}
trap restart EXIT
# shellcheck disable=SC2086 # one service name per word
docker compose stop $services

for entry in "${entries[@]}"; do
  IFS=$'\t' read -r archive service mount <<<"$entry"
  id=$(docker compose ps --all --quiet "$service")
  echo "Restoring $service $mount"
  # tar runs as root in the container, so files get back their original owners and modes.
  docker run --rm -i --network none --volumes-from "$id" "$busybox" \
    sh -c 'find "$1" -mindepth 1 -delete && tar -xzf - -C "$1"' restore "$mount" <"$backup/$archive"
done
echo "Restored. Services that weren't running before stay stopped: start them with 'docker compose up -d'."
