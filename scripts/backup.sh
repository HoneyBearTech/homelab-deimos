#!/usr/bin/env bash
# Back up the stack's state: every service's data mounts (each read-write volume or directory it mounts, such as
# Tdarr's or Spoolman's database; never the Docker socket, and never a path the service's
# org.honeybeartech.deimos.backup.skip label excludes, such as the media library) and the settings files. Paths a
# service's org.honeybeartech.deimos.backup.exclude label lists inside a mount are left out of that mount's archive.
# Each service is stopped only while its own data is copied, so its database is consistent, and started again before
# the copy is compressed; services with nothing to archive keep running.
#
#   scripts/backup.sh [DIR]      DIR defaults to backups/<date>-<time> in the checkout (gitignored)
#
# DIR gets one <service>--<path>.tar.gz per mount, the settings (.env and any <service>.env) under env/, a MANIFEST
# naming each archive's service, container path, source and image, NETWORKS with the address range of each network
# the stack uses but doesn't create (so a rebuilt host can recreate it the same), VERSION (the checkout's
# `git describe`), and SHA256SUMS, all readable only by the user who ran it: the archives hold logins, tokens and
# private keys. Copy it off the host (scripts/scheduled-backup.sh does). Restore with scripts/restore.sh. It runs
# `docker compose` from the checkout, so the standard Compose variables (COMPOSE_PROJECT_NAME, COMPOSE_FILE,
# COMPOSE_ENV_FILES) select another project, as the smoke test does. On a host where the pull agent deploys the
# stack, it first waits for a deploy in progress to finish and keeps the agent from deploying until it's done
# (deploy_lock in scripts/lib.sh).
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=scripts/lib.sh
. "$root/scripts/lib.sh"
cd "$root"
# Before anything stops: no deploy by the pull agent runs while services are stopped or their data is replaced.
deploy_lock
dest=${1:-backups/$(date +%Y%m%d-%H%M%S)}
# pigz compresses on every core; gzip on one is several times slower on large archives.
compress=$(command -v pigz || command -v gzip)

if [ -e "$dest" ] && [ -n "$(ls -A "$dest")" ]; then
  echo "error: $dest already exists and isn't empty" >&2
  exit 1
fi
umask 077
mkdir -p "$dest/env"
dest=$(cd "$dest" && pwd)

# Every service needs a container (running or stopped) to read its mounts from.
services=()
for service in $(docker compose config --services); do
  if [ -z "$(docker compose ps --all --quiet "$service")" ]; then
    echo "error: $service has no container; run 'docker compose create' or 'docker compose up -d' first" >&2
    exit 1
  fi
  # Checked before anything stops, so a wrong label fails the backup with every service still running.
  check_excludes "$(docker compose ps --all --quiet "$service")"
  services+=("$service")
done

# What was running when the backup began. start_again starts whichever of it is stopped now: the service stopped for
# its backup, and any service Compose stopped along with it (one that depends on it). On exit every one of them must
# be running again, or the backup fails and names them (the scheduled backup then reports it down).
running=$(docker compose ps --services --status running)

# A stop can fail while the container is still on its way down: when its process is stuck writing to a network share
# (NFS), Docker gives up waiting for it to exit ("tried to kill container, but did not receive an exit event").
# Starting it again then does nothing, as it still runs, and once it exits nothing restarts it: `restart:
# unless-stopped` doesn't after a stop, and autoheal only restarts running containers. So after a failed stop the
# backup waits up to BACKUP_STOP_WAIT seconds (default 120) for the container to exit.
stop_wait=${BACKUP_STOP_WAIT:-120}
if ! [[ $stop_wait =~ ^[0-9]+$ ]]; then
  echo "error: BACKUP_STOP_WAIT must be a number of seconds, not '$stop_wait'" >&2
  exit 1
fi
# The service (and its container) whose stop failed and that was still running stop_wait seconds later.
stuck=""
stuck_id=""

# Waits up to $2 seconds for container $1 to exit; fails if it's still running then.
wait_exited() {
  local deadline=$((SECONDS + $2))
  while [ "$(docker inspect --format '{{.State.Running}}' "$1")" = true ]; do
    if [ "$SECONDS" -ge "$deadline" ]; then return 1; fi
    sleep 1
  done
}

# Stops service $1 (container $2) for its backup. If the stop fails but the container exits within stop_wait, the
# backup goes on: its data is as after a crash, which is what the app would start from anyway. If it still runs then,
# the backup fails, and start_again waits for it once more before starting anything.
stop_service() {
  if docker compose stop "$1"; then return 0; fi
  echo "warning: stopping $1 failed; waiting up to ${stop_wait}s for its container to exit" >&2
  if wait_exited "$2" "$stop_wait"; then
    echo "warning: $1 exited after its stop failed; backing it up as it was left" >&2
    return 0
  fi
  stuck=$1
  stuck_id=$2
  echo "error: $1 still runs ${stop_wait}s after its stop failed; the backup stops here" >&2
  return 1
}

# Starts again what was running and is stopped now, once no container is still on its way down (Compose would skip
# it, and it would stay down once it exited).
start_again() {
  if [ -n "$stuck" ]; then
    echo "Waiting up to ${stop_wait}s more for $stuck to exit before starting the services again"
    if wait_exited "$stuck_id" "$stop_wait"; then stuck=""; fi
  fi
  if [ -n "$running" ]; then
    # shellcheck disable=SC2086 # one service name per word
    docker compose start $running
  fi
}

# Fails, naming them, unless every service that was running when the backup began is running again.
check_running() {
  local now service down=""
  now=$(docker compose ps --services --status running)
  for service in $running; do
    if [[ $'\n'"$now"$'\n' != *$'\n'"$service"$'\n'* ]]; then down="$down $service"; fi
  done
  if [ -n "$down" ]; then
    echo "error: not running again after the backup:$down (start them: docker compose start$down)" >&2
    return 1
  fi
  if [ -n "$stuck" ]; then
    echo "error: $stuck never exited after its stop failed; it may still go down: check it, and start it again" \
      "(docker compose start $stuck) if it has stopped" >&2
    return 1
  fi
}
finish() {
  status=$?
  start_again || status=1
  check_running || status=1
  exit "$status"
}
trap finish EXIT

printf '# archive\tservice\tmount\tsource\timage\n' >"$dest/MANIFEST"
for service in "${services[@]}"; do
  id=$(docker compose ps --all --quiet "$service")
  image=$(docker inspect --format '{{.Config.Image}}' "$id")
  while IFS= read -r mount; do
    echo "Skipping $service $mount (excluded from backups by its $skip_label label)"
  done < <(skipped_mounts "$id")
  mounts=()
  while IFS=$'\t' read -r mount source; do
    if is_dir "$id" "$mount"; then
      mounts+=("$mount"$'\t'"$source")
    else
      echo "Skipping $service $mount (not a directory)"
    fi
  done < <(data_mounts "$id")
  if [ ${#mounts[@]} -eq 0 ]; then continue; fi

  if [[ $'\n'"$running"$'\n' == *$'\n'"$service"$'\n'* ]]; then
    echo "Stopping $service"
    stop_service "$service" "$id"
  fi
  archives=()
  for entry in "${mounts[@]}"; do
    IFS=$'\t' read -r mount source <<<"$entry"
    archive=$(archive_name "$service" "$mount")
    echo "Archiving $service $mount ($source)"
    # A mount inside this one (such as a cache mounted inside a service's data directory) is archived or skipped on
    # its own, and the paths the service's exclude label lists are left out.
    excludes=()
    while IFS= read -r path; do
      echo "  leaving out $path"
      excludes+=("--exclude=./${path#"$mount"/}")
    done < <(left_out "$id" "$mount")
    # tar runs as root in the container so it can read every file; the archive itself is written by this shell,
    # so it belongs to the user running the backup. Uncompressed here: compressing takes far longer than copying,
    # and the service stays stopped only for the copy. No log driver: Docker's default one would also write the whole
    # archive, which arrives on stdout, into the container's log, and the copy would take several times as long.
    docker run --rm --log-driver none --network none --volumes-from "$id:ro" "$busybox" \
      tar -cf - "${excludes[@]+"${excludes[@]}"}" -C "$mount" . </dev/null >"$dest/${archive%.gz}"
    printf '%s\t%s\t%s\t%s\t%s\n' "$archive" "$service" "$mount" "$source" "$image" >>"$dest/MANIFEST"
    archives+=("$archive")
  done
  start_again
  for archive in "${archives[@]}"; do
    "$compress" -c "$dest/${archive%.gz}" >"$dest/$archive"
    rm "$dest/${archive%.gz}"
  done
done

# The networks the stack joins but doesn't create (external ones, even if another Compose project once created
# them): their address range, which access lists may depend on, is lost with the host unless it's written down.
project_label='{{index .Labels "com.docker.compose.project"}}'
ranges='{{range .IPAM.Config}}{{$.Name}}{{"\t"}}{{.Subnet}}{{"\t"}}{{.Gateway}}{{"\n"}}{{end}}'
project=$(docker inspect --format "{{index .Config.Labels \"com.docker.compose.project\"}}" \
  "$(docker compose ps --all --quiet "${services[0]}")")
networks=$(for service in "${services[@]}"; do
  docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
    "$(docker compose ps --all --quiet "$service")"
done | sort -u)
printf '# network\tsubnet\tgateway\n' >"$dest/NETWORKS"
for network in $networks; do
  if [ "$(docker network inspect --format "$project_label" "$network")" = "$project" ]; then continue; fi
  docker network inspect --format "$ranges" "$network" | grep . >>"$dest/NETWORKS" || true
done
git -C "$root" describe --tags --always --dirty >"$dest/VERSION" 2>/dev/null || echo unknown >"$dest/VERSION"

# The settings: .env and any service's env file, or only the files in COMPOSE_ENV_FILES when that's set (the smoke
# test, which must never copy the real ones).
if [ -n "${COMPOSE_ENV_FILES:-}" ]; then
  IFS=, read -r -a env_files <<<"$COMPOSE_ENV_FILES"
else
  env_files=(.env *.env)
fi
for file in "${env_files[@]}"; do
  if [ -f "$file" ]; then cp "$file" "$dest/env/$(basename "$file")"; fi
done

files=$(cd "$dest" && find . -type f | sed 's|^\./||' | sort)
(cd "$dest" && while read -r f; do sha256 "$f"; done <<<"$files" >SHA256SUMS)
echo "Backup written to $dest ($(du -sh "$dest" | cut -f1)). It holds secrets: copy it off the host, privately."
