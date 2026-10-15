#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh: replace the contents of each data mount listed in the backup's
# MANIFEST with its archive. The services are stopped while their data is replaced, and whatever was running is
# started again afterwards.
#
#   scripts/restore.sh [--yes] DIR [SERVICE...]
#
# Without SERVICE, every service that has an archive in DIR is restored. The checksums are verified first, and it
# asks for confirmation (unless --yes) after listing what it will overwrite. It only writes to a container path
# the service still mounts read-write, never to the Docker socket or a path the service excludes from backups, and
# leaves the paths a service's exclude label lists inside a mount as they are. An archive goes to whatever
# compose.yaml mounts at its service's container path now, whatever source the MANIFEST names (a backup of a data
# directory that has since moved under CONFIG_ROOT restores into the new folder): a container created from an older
# compose.yaml is recreated first, and a directory that doesn't exist yet is created. On a new host, restore .env from
# DIR/env first; the containers, and the volumes and directories they mount, are created as needed. Uses
# `docker compose` from the checkout, so the standard Compose variables select another project, as in
# scripts/backup.sh. Like backup.sh, it waits for a deploy by the pull agent to finish and keeps the agent from
# deploying until it's done (deploy_lock in scripts/lib.sh).
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
# Before anything stops: no deploy by the pull agent runs while services are stopped or their data is replaced.
deploy_lock

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

# Whether service $1's container was created from compose.yaml as it is now: Compose labels each container with a
# hash of its service's configuration.
current() {
  local id
  id=$(docker compose ps --all --quiet "$1")
  [ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$id")" = \
    "$(docker compose config --hash "$1" | awk -v s="$1" '$1 == s { print $2 }')" ]
}

# Checks that service $1's mount at $2 may be replaced, and prints what it mounts there (a volume or a host
# directory) and what in it a restore leaves alone.
target() {
  local id source keep
  id=$(docker compose ps --all --quiet "$1")
  if skipped_mounts "$id" | grep -qxF -- "$2"; then
    fail "$1 excludes $2 from backups ($skip_label); refusing to write it"
  fi
  check_excludes "$id" || exit 1
  source=$(data_mounts "$id" | awk -F '\t' -v m="$2" '$1 == m { print $2 }')
  [ -n "$source" ] || fail "$1 has no read-write mount at $2 any more (compose.yaml changed since the backup)"
  keep=$(left_out "$id" "$2" | tr '\n' ',' | sed -e 's/,$//' -e 's/,/, /g')
  echo "$source${keep:+ (except $keep)}"
}

# The archives go where compose.yaml mounts each container path now, not to the source the MANIFEST names: a
# container created from an older compose.yaml (one whose data was in another directory, say) is recreated once its
# service has stopped, and checked then.
stale=""
echo "This replaces everything in:"
for entry in "${entries[@]}"; do
  IFS=$'\t' read -r archive service mount <<<"$entry"
  if current "$service"; then
    where=$(target "$service" "$mount")
  else
    where="what compose.yaml mounts there (the container predates compose.yaml and is recreated first)"
    case " $stale " in *" $service "*) ;; *) stale="$stale $service" ;; esac
  fi
  echo "  $service $mount: $where"
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

if [ -n "$stale" ]; then
  echo "Recreating from compose.yaml:$stale"
  # shellcheck disable=SC2086 # one service name per word
  docker compose create $stale
fi
# Before anything is written: the recreated containers pass the same checks, and every mount is a directory. A
# directory that doesn't exist yet (a new host, a folder not made yet) is created here by Docker, as for any bind
# mount (owned by root, mode 0755); the archive's top entry then gives it its owner and mode.
for entry in "${entries[@]}"; do
  IFS=$'\t' read -r archive service mount <<<"$entry"
  case " $stale " in
    *" $service "*)
      where=$(target "$service" "$mount")
      echo "  $service $mount: $where"
      ;;
  esac
  docker run --rm --network none --volumes-from "$(docker compose ps --all --quiet "$service")" "$busybox" \
    test -d "$mount" </dev/null || fail "$service $mount isn't a directory"
done

for entry in "${entries[@]}"; do
  IFS=$'\t' read -r archive service mount <<<"$entry"
  id=$(docker compose ps --all --quiet "$service")
  echo "Restoring $service $mount"
  # A mount inside this one is restored or skipped on its own, and the paths the service's exclude label lists
  # aren't in the archive: neither they nor the directories leading to them are deleted. (-delete implies -depth,
  # so -prune can't be used; the patterns exclude them instead.)
  spare=()
  while IFS= read -r nested; do
    spare+=(! -path "$nested" ! -path "$nested/*")
    parent=${nested%/*}
    while [ "$parent" != "$mount" ]; do
      spare+=(! -path "$parent")
      parent=${parent%/*}
    done
  done < <(left_out "$id" "$mount")
  docker run --rm --network none --volumes-from "$id" "$busybox" \
    find "$mount" -mindepth 1 "${spare[@]+"${spare[@]}"}" -delete </dev/null
  # tar runs as root in the container, so files get back their original owners and modes.
  docker run --rm -i --network none --volumes-from "$id" "$busybox" tar -xzf - -C "$mount" <"$backup/$archive"
done
echo "Restored. Services that weren't running before stay stopped: start them with 'docker compose up -d'."
