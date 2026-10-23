#!/usr/bin/env bash
# Smoke test of the stack: start every service in compose.yaml with throwaway settings, wait until each one reports
# healthy, then mark every data mount, take a backup, change the data, restore the backup and check that the data
# and health came back (and that mounts and paths excluded from backups were neither archived nor overwritten, that
# an app's config folder that is missing, behind a container made from older settings, is recreated where
# compose.yaml says, and that a test container that mounts a folder inside another app's data is started again with
# it), run the scheduled backup against a stand-in backup server, make a test container unhealthy and check that
# autoheal restarts it (when the stack has autoheal), and remove everything it created. CI runs it on every change;
# `make smoke` runs it locally.
# Without a compose.yaml there is nothing to test, and it says so and passes.
#
# It stays away from any real installation on the same Docker host: its own Compose project, settings from
# .env.example with every directory (*_ROOT, *_PATH; so CONFIG_ROOT, with the apps' folders, too) moved into a
# temporary directory, every volume and network name (*_VOLUME, *_NETWORK) and name prefix (*_PREFIX) replaced by one
# of its own, every address range (*_SUBNET) by one Docker has free, no fixed container names, no published ports and
# no service env files. It never reads .env, ignores the stack's settings in the environment, and on exit removes only
# the volumes Compose labelled with its own project and the networks it created. A stack's own steps (throwaway
# secrets, checks of what its services expose) go in scripts/smoke-stack.sh, when that exists.
set -euo pipefail

project=homelab-deimos-smoke
timeout=${SMOKE_TIMEOUT:-300}
root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=scripts/lib.sh
. "$root/scripts/lib.sh"
if [ -f "$root/scripts/smoke-stack.sh" ]; then
  # shellcheck source=/dev/null # only in a stack that has its own steps
  . "$root/scripts/smoke-stack.sh"
fi

if [ ! -f "$root/compose.yaml" ]; then
  message="No compose.yaml yet; nothing to smoke-test"
  if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::notice::$message"; else echo "$message"; fi
  exit 0
fi
work=$(mktemp -d)

# The settings come from the generated env file only, never from the caller's environment. The Compose variables
# also point scripts/backup.sh and scripts/restore.sh at this project.
settings=$(grep -E '^[A-Z][A-Z0-9_]*=' "$root/.env.example" || true)
while IFS='=' read -r key _; do
  if [ -n "$key" ]; then unset "$key"; fi
done <<<"$settings"
unset COMPOSE_PROFILES
export COMPOSE_PROJECT_NAME=$project
export COMPOSE_FILE=$root/compose.yaml:$work/override.yaml
export COMPOSE_ENV_FILES=$work/env

compose() { docker compose "$@"; }

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    compose ps --all || true
    compose logs --no-color --tail 50 || true
  fi
  compose down --remove-orphans --timeout 30 || true
  docker volume ls -q --filter "label=com.docker.compose.project=$project" |
    xargs -r docker volume rm >/dev/null || true
  for network in "${networks[@]+"${networks[@]}"}"; do docker network rm "$network" >/dev/null || true; done
  # Services that run as root leave root-owned files in the throwaway directories; remove them as root too.
  docker run --rm --network none -v "$work:/work" "$busybox" find /work -mindepth 1 -delete </dev/null || true
  rm -rf "$work"
  exit "$status"
}
networks=()
trap cleanup EXIT

# An address range (a /28) that no Docker network on the host uses, nor any range given as an argument, from
# 172.16.0.0/16: outside Docker's default address pools (172.17-31.0.0/16, 192.168.0.0/16), so a network Compose
# creates meanwhile can't be given it.
free_subnet() {
  # shellcheck disable=SC2046 # one network id per word
  docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' $(docker network ls -q) |
    python3 -I -c '
import ipaddress, sys
used = [ipaddress.ip_network(s) for s in sys.stdin.read().split() + sys.argv[1:] if "." in s]
for n in range(255, -1, -1):
    candidate = ipaddress.ip_network(f"172.16.{n}.0/28")
    if not any(candidate.overlaps(u) for u in used):
        print(candidate)
        break
else:
    sys.exit("no free /28 in 172.16.0.0/16")' "$@"
}
subnets=()

data_dirs=()
while IFS= read -r line; do
  [ -n "$line" ] || continue
  key=${line%%=*}
  case $key in
    *_ROOT | *_PATH)
      value=$work/$(echo "$key" | tr 'A-Z_' 'a-z-')
      mkdir -p "$value"
      data_dirs+=("$value")
      ;;
    *_PREFIX) value=${project}_ ;;
    *_VOLUME) value=${project}_$(echo "$key" | tr 'A-Z_' 'a-z-') ;;
    *_NETWORK) value=${project}_$(echo "$key" | tr 'A-Z_' 'a-z-') ;;
    # A network's address range, which the stack fixes (for an access list), mustn't overlap one already on the host.
    *_SUBNET)
      value=$(free_subnet "${subnets[@]+"${subnets[@]}"}")
      subnets+=("$value")
      ;;
    *) value=${line#*=} ;;
  esac
  printf '%s=%s\n' "$key" "$value"
done <<<"$settings" >"$work/env"

# The folders the stack bind-mounts inside those directories (each app's CONFIG_ROOT/<app>/<purpose>) are made here
# too, as the installing docs have the user make them, so they're owned like the directories rather than by root.
while IFS= read -r dir; do
  mkdir -p "$dir"
  data_dirs+=("$dir")
done < <(COMPOSE_FILE=$root/compose.yaml docker compose config |
  awk -v w="$work/" '$1 == "source:" && index($2, w) == 1 { print $2 }' | sort -u)

# The app whose data a test container follows (follower-test, below): the last one with a config folder under
# CONFIG_ROOT, so not the one the restore round trip moves elsewhere. The folder it follows is made, and owned, like
# the others.
config_root=$(sed -n 's/^CONFIG_ROOT=//p' "$work/env")
follow_leader=""
for dir in "${data_dirs[@]+"${data_dirs[@]}"}"; do
  case $dir in
    "$config_root"/*/config)
      follow_leader=${dir#"$config_root"/}
      follow_leader=${follow_leader%/config}
      ;;
  esac
done
if [ -n "$follow_leader" ]; then
  mkdir -p "$config_root/$follow_leader/config/smoke-follower"
  data_dirs+=("$config_root/$follow_leader/config/smoke-follower")
fi

# The directories are owned as on a host: by PUID:PGID with mode 0755 when the stack sets PUID (the LinuxServer.io
# images' convention), so a service that needs more than that, such as DAC_OVERRIDE to write into a directory it
# doesn't own, fails here rather than at a deploy. Without PUID they're writable by any user: some services run as
# a fixed uid that isn't the caller's. chown runs as root in a throwaway container.
puid=$(sed -n 's/^PUID=//p' "$work/env")
pgid=$(sed -n 's/^PGID=//p' "$work/env")
for dir in "${data_dirs[@]+"${data_dirs[@]}"}"; do
  if [ -n "$puid" ]; then
    docker run --rm --network none -v "$dir:/data" "$busybox" \
      sh -c 'chown "$1:$2" /data && chmod 0755 /data' own "$puid" "${pgid:-$puid}" </dev/null
  else
    chmod 0777 "$dir"
  fi
done

# Networks the stack expects to exist (external ones, named by *_NETWORK settings) are created for the test only.
while IFS='=' read -r key value; do
  case $key in
    *_NETWORK)
      docker network create "$value" >/dev/null
      networks+=("$value")
      ;;
  esac
done <"$work/env"

# Project-scoped container names, no published ports and no env files, so the test can't collide with anything
# already running on the host or read a real secret; the health checks run inside the containers and need neither.
stack_services=$(COMPOSE_FILE=$root/compose.yaml docker compose config --no-interpolate --services)
has_autoheal=false
{
  echo "services:"
  for service in $stack_services; do
    printf '  %s:\n    container_name: !reset null\n    ports: !reset []\n    env_file: !reset []\n' "$service"
    if [ "$service" = autoheal ]; then has_autoheal=true; fi
  done
  # A container that turns unhealthy on demand (a file in its tmpfs), for autoheal to restart.
  if $has_autoheal; then
    cat <<YAML
  autoheal-test:
    image: $busybox
    command: ["sleep", "infinity"]
    init: true
    tmpfs: [/tmp]
    labels:
      autoheal: "true"
    healthcheck:
      test: ["CMD", "sh", "-c", "test ! -e /tmp/unhealthy"]
      interval: 2s
      retries: 2
YAML
  fi
  # A container that bind-mounts a folder inside another app's data, read-only, as a worker reads its API's folder:
  # a restore of that app must stop it and start it again, or it keeps seeing the folder it replaced.
  if [ -n "$follow_leader" ]; then
    cat <<YAML
  follower-test:
    image: $busybox
    command: ["sleep", "infinity"]
    init: true
    volumes:
      - $config_root/$follow_leader/config/smoke-follower:/follow:ro
YAML
  fi
} >"$work/override.yaml"

compose config --quiet
if declare -F smoke_prepare >/dev/null; then smoke_prepare; fi
if [ -f "$work/override-stack.yaml" ]; then export COMPOSE_FILE=$COMPOSE_FILE:$work/override-stack.yaml; fi
echo "Starting $(compose config --services | wc -l | tr -d ' ') services," \
  "waiting up to ${timeout}s for them to be healthy"
# One retry: a service that fetches something from the internet as it starts can fail on the network, not the
# stack. A service that is really broken fails both attempts.
if ! compose up --detach --quiet-pull --wait --wait-timeout "$timeout"; then
  echo "Not every service became healthy; retrying once"
  compose up --detach --wait --wait-timeout "$timeout"
fi
compose ps --format 'table {{.Service}}\t{{.Status}}'

fail() {
  echo "error: $*" >&2
  exit 1
}
if declare -F smoke_check >/dev/null; then smoke_check; fi

# A marker file in a data mount, written and read in a throwaway container (the service's image may have no shell).
put_marker() {
  docker run --rm --network none --volumes-from "$1" "$busybox" \
    sh -c 'echo "$2" >"$1/.smoke-marker" && if [ "$2" = changed ]; then touch "$1/.smoke-stray"; fi' \
    marker "$2" "$3" </dev/null
}
# The owner and mode of a path in container $1, read in a throwaway container (the container may be stopped).
owner_mode() {
  docker run --rm --network none --volumes-from "$1:ro" "$busybox" stat -c '%u:%g %a' "$2" </dev/null
}
check_marker() {
  docker run --rm --network none --volumes-from "$1:ro" "$busybox" \
    sh -c 'test "$(cat "$1/.smoke-marker")" = backed-up && test ! -e "$1/.smoke-stray"' check "$2" </dev/null
}

# Every directory a backup would archive, as "<service><TAB><mount>".
targets=()
for service in $(compose config --services); do
  id=$(compose ps --all --quiet "$service")
  while IFS=$'\t' read -r mount _; do
    if is_dir "$id" "$mount"; then targets+=("$service	$mount"); fi
  done < <(data_mounts "$id")
done
# Every directory a service excludes from backups (its backup.skip label): never archived, never overwritten.
skipped=()
for service in $(compose config --services); do
  id=$(compose ps --all --quiet "$service")
  while IFS= read -r mount; do
    if is_dir "$id" "$mount"; then skipped+=("$service	$mount"); fi
  done < <(skipped_mounts "$id")
done
# Every path a service leaves out of a mount's archive (its backup.exclude label): not archived, not deleted.
excluded=()
for service in $(compose config --services); do
  id=$(compose ps --all --quiet "$service")
  while IFS= read -r path; do excluded+=("$service	$path"); done < <(label_paths "$id" "$exclude_label")
done
if [ ${#targets[@]} -eq 0 ]; then
  echo "No service has a data mount; skipping the backup and restore round trip"
else
  for target in "${skipped[@]+"${skipped[@]}"}"; do
    IFS=$'\t' read -r service mount <<<"$target"
    docker run --rm --network none --volumes-from "$(compose ps --quiet "$service")" "$busybox" \
      sh -c 'echo kept >"$1/.smoke-skipped"' mark "$mount" </dev/null
  done
  for entry in "${excluded[@]+"${excluded[@]}"}"; do
    IFS=$'\t' read -r service path <<<"$entry"
    docker run --rm --network none --volumes-from "$(compose ps --quiet "$service")" "$busybox" \
      sh -c 'mkdir -p "$1" && echo kept >"$1/.smoke-excluded"' mark "$path" </dev/null
  done
  echo "Backup and restore round trip over ${#targets[@]} data mount(s)"
  for target in "${targets[@]}"; do
    IFS=$'\t' read -r service mount <<<"$target"
    put_marker "$(compose ps --quiet "$service")" "$mount" backed-up
  done
  follow_marker() {
    docker run --rm --network none -v "$config_root/$follow_leader/config:/data" "$busybox" \
      sh -c 'echo "$1" >/data/smoke-follower/.smoke-follow' mark "$1" </dev/null
  }
  if [ -n "$follow_leader" ]; then follow_marker backed-up; fi
  "$root/scripts/backup.sh" "$work/backup"
  for network in "${networks[@]+"${networks[@]}"}"; do
    grep -Eq "^$network"$'\t'"[0-9a-f.:]+/[0-9]+"$'\t' "$work/backup/NETWORKS" ||
      fail "the backup's NETWORKS doesn't record the address range of $network"
  done
  # NETWORKS is written even when the stack joins no network it doesn't create (then it has only its header).
  [ -f "$work/backup/NETWORKS" ] || fail "the backup has no NETWORKS"
  [ -s "$work/backup/VERSION" ] || fail "the backup has no VERSION"
  [ "$(compose ps --services --status running | wc -l)" -eq "$(compose config --services | wc -l)" ] ||
    fail "backup.sh didn't start every service again"

  for target in "${targets[@]}"; do
    IFS=$'\t' read -r service mount <<<"$target"
    put_marker "$(compose ps --quiet "$service")" "$mount" changed
  done
  if [ -n "$follow_leader" ]; then follow_marker changed; fi
  # A restore writes where compose.yaml mounts a service's data now, whatever its container or the backup's MANIFEST
  # names, and creates a folder that is missing (a new host, or the move of the data under CONFIG_ROOT): one app's
  # container is recreated with its data somewhere else, as from older settings, and its config folder
  # (CONFIG_ROOT/<app>/config, a mount with nothing left out of it) is removed. Not the app follower-test follows, nor
  # one with Docker secrets (their files, under CONFIG_ROOT too, would be missing).
  moved=""
  for target in "${targets[@]}"; do
    IFS=$'\t' read -r service mount <<<"$target"
    id=$(compose ps --quiet "$service")
    source=$(data_mounts "$id" | awk -F '\t' -v m="$mount" '$1 == m { print $2 }')
    app=${source#"$config_root"/}
    if [ -z "$moved" ] && [ -n "$config_root" ] && [ "$source" = "$config_root/${app%/config}/config" ] &&
      [[ "${app%/config}" != */* ]] && [ "${app%/config}" != "$follow_leader" ] &&
      [ -z "$(left_out "$id" "$mount")" ] &&
      ! docker inspect --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' "$id" | grep -q '^/run/secrets/'; then
      moved=$service
      moved_mount=$mount
      moved_dir=$app
    fi
  done
  if [ -n "$config_root" ] && [ -z "$moved" ]; then fail "no app keeps its data in CONFIG_ROOT/<app>/config"; fi
  if [ -n "$moved" ]; then
    moved_owner=$(owner_mode "$(compose ps --quiet "$moved")" "$moved_mount")
    compose stop "$moved"
    CONFIG_ROOT=$work/elsewhere docker compose create --force-recreate "$moved"
    docker run --rm --network none -v "$config_root:/config-root" "$busybox" rm -rf "/config-root/$moved_dir" \
      </dev/null
    echo "$moved: container recreated with its data elsewhere, and its config folder removed"
  fi
  "$root/scripts/restore.sh" --yes "$work/backup"
  for target in "${skipped[@]+"${skipped[@]}"}"; do
    IFS=$'\t' read -r service mount <<<"$target"
    if awk -F '\t' -v s="$service" -v m="$mount" '$2 == s && $3 == m { found = 1 } END { exit !found }' \
      "$work/backup/MANIFEST"; then
      fail "$service $mount is excluded from backups but was archived"
    fi
    docker run --rm --network none --volumes-from "$(compose ps --all --quiet "$service"):ro" "$busybox" \
      sh -c 'test "$(cat "$1/.smoke-skipped")" = kept' check "$mount" </dev/null ||
      fail "$service $mount is excluded from backups but restore.sh touched it"
  done
  if [ ${#skipped[@]} -gt 0 ]; then echo "${#skipped[@]} excluded mount(s) were neither archived nor overwritten"; fi
  for entry in "${excluded[@]+"${excluded[@]}"}"; do
    IFS=$'\t' read -r service path <<<"$entry"
    while IFS=$'\t' read -r archive s mount _; do
      if [ "$s" = "$service" ] && [[ "$path" == "$mount"/* ]] &&
        grep -qF -- "./${path#"$mount"/}/" <(tar -tzf "$work/backup/$archive"); then
        fail "$service $path is left out of backups but is in $archive"
      fi
    done <"$work/backup/MANIFEST"
    docker run --rm --network none --volumes-from "$(compose ps --all --quiet "$service"):ro" "$busybox" \
      sh -c 'test "$(cat "$1/.smoke-excluded")" = kept' check "$path" </dev/null ||
      fail "$service $path is left out of backups but restore.sh deleted it"
  done
  if [ ${#excluded[@]} -gt 0 ]; then echo "${#excluded[@]} left-out path(s) were neither archived nor deleted"; fi

  for target in "${targets[@]}"; do
    IFS=$'\t' read -r service mount <<<"$target"
    check_marker "$(compose ps --all --quiet "$service")" "$mount" ||
      fail "$service $mount wasn't restored exactly (marker changed, or a file not in the backup was left)"
  done
  if [ -n "$moved" ]; then
    [ ! -e "$work/elsewhere" ] || fail "restore.sh wrote to the mount of $moved's out-of-date container"
    [ "$(owner_mode "$(compose ps --all --quiet "$moved")" "$moved_mount")" = "$moved_owner" ] ||
      fail "restore.sh didn't create $moved's config folder again with its owner and mode"
    echo "$moved's data was restored into its config folder, created again, through a recreated container"
  fi
  if [ -n "$follow_leader" ]; then
    [ "$(compose exec -T follower-test cat /follow/.smoke-follow 2>/dev/null)" = backed-up ] ||
      fail "follower-test doesn't see $follow_leader's restored folder (restore.sh didn't restart it with $follow_leader)"
    echo "follower-test, which mounts a folder inside $follow_leader's data, sees the restored folder"
  fi
  compose up --detach --wait --wait-timeout "$timeout"
  echo "Every data mount was restored, and every service is healthy again"
  if declare -F smoke_check >/dev/null; then smoke_check; fi
fi

# The unattended backup, copying to a stand-in for the backup server (a local directory, which rsync treats the same
# way) that already holds two old backups and a directory that isn't one; keeping two there and one here must leave
# the newer old one and the new one there, only the new one here, and the other directory alone.
if [ ${#targets[@]} -gt 0 ]; then
  echo "Scheduled backup: copy off the host and prune old backups"
  mkdir -p "$work/scheduled/20200101-000000" "$work/remote/20200101-000000" "$work/remote/20200102-000000" \
    "$work/remote/keep-me"
  printf 'BACKUP_DIR=%s\nBACKUP_KEEP=1\nBACKUP_REMOTE=%s\nBACKUP_REMOTE_KEEP=2\n' "$work/scheduled" "$work/remote" \
    >"$work/backup.env"
  BACKUP_ENV_FILE=$work/backup.env "$root/scripts/scheduled-backup.sh"
  entries() { find "$1" -mindepth 1 -maxdepth 1 -exec basename {} \; | sort | tr '\n' ' '; }
  new=$(entries "$work/scheduled" | tr -d ' ')
  [[ $new =~ ^[0-9]{8}-[0-9]{6}$ ]] || fail "expected only the new backup in BACKUP_DIR, found: $new"
  [ "$(entries "$work/remote")" = "20200102-000000 $new keep-me " ] ||
    fail "wrong backups kept at the remote: $(entries "$work/remote")"
  (cd "$work/remote/$new" && sha256 -c --quiet SHA256SUMS) || fail "the copy off the host doesn't match its SHA256SUMS"
  [ -z "$(find "$work/remote/$new" -perm -004)" ] || fail "the copy off the host is readable by other users"
  [ "$(compose ps --services --status running | wc -l)" -eq "$(compose config --services | wc -l)" ] ||
    fail "scheduled-backup.sh didn't leave every service running"
  echo "The scheduled backup was copied intact, and only the newest backups were kept"
fi

if $has_autoheal; then
  echo "autoheal: making a test container unhealthy"
  test_id=$(compose ps --quiet autoheal-test)
  started=$(docker inspect --format '{{.State.StartedAt}}' "$test_id")
  compose exec -T autoheal-test touch /tmp/unhealthy
  for _ in $(seq 1 40); do
    sleep 3
    [ "$(docker inspect --format '{{.State.StartedAt}}' "$test_id")" = "$started" ] || break
  done
  [ "$(docker inspect --format '{{.State.StartedAt}}' "$test_id")" != "$started" ] ||
    fail "autoheal didn't restart the unhealthy container within 2 minutes"
  compose logs --no-color autoheal | grep -q "found to be unhealthy" || fail "autoheal didn't log the restart"
  compose up --detach --wait --wait-timeout 60 autoheal-test
  echo "autoheal restarted the unhealthy container through socket-proxy, and it's healthy again"
fi
