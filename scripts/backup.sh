#!/usr/bin/env bash
# Back up the stack's state: every service's data mounts (each read-write volume or directory it mounts, such as
# Tdarr's or Spoolman's database; never the Docker socket, and never a path the service's
# org.honeybeartech.deimos.backup.skip label excludes, such as the media library) and the settings files. Each
# service is stopped only while its own archives are written, so its database is consistent, and started again
# afterwards if it was running; services with nothing to archive keep running.
#
#   scripts/backup.sh [DIR]      DIR defaults to backups/<date>-<time> in the checkout (gitignored)
#
# DIR gets one <service>--<path>.tar.gz per mount, the settings (.env and any <service>.env) under env/, a MANIFEST
# naming each archive's service, container path, source and image, NETWORKS with the address range of each network
# the stack uses but doesn't create (so a rebuilt host can recreate it the same), VERSION (the checkout's
# `git describe`), and SHA256SUMS, all readable only by the user who ran it: the archives hold logins, tokens and
# private keys. Copy it off the host. Restore with
# scripts/restore.sh. It runs `docker compose` from the checkout, so the standard Compose variables
# (COMPOSE_PROJECT_NAME, COMPOSE_FILE, COMPOSE_ENV_FILES) select another project, as the smoke test does.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=scripts/lib.sh
. "$root/scripts/lib.sh"
cd "$root"
dest=${1:-backups/$(date +%Y%m%d-%H%M%S)}

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
  services+=("$service")
done

running=$(docker compose ps --services --status running)
stopped=""
restart() {
  status=$?
  if [ -n "$stopped" ]; then
    echo "Starting $stopped"
    docker compose start "$stopped" || status=1
  fi
  exit "$status"
}
trap restart EXIT

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

  case $'\n'"$running"$'\n' in
    *$'\n'"$service"$'\n'*)
      echo "Stopping $service"
      stopped=$service
      docker compose stop "$service"
      ;;
  esac
  for entry in "${mounts[@]}"; do
    IFS=$'\t' read -r mount source <<<"$entry"
    archive=$(archive_name "$service" "$mount")
    echo "Archiving $service $mount ($source)"
    # tar runs as root in the container so it can read every file; the archive itself is written by this shell,
    # so it belongs to the user running the backup.
    docker run --rm --network none --volumes-from "$id:ro" "$busybox" tar -czf - -C "$mount" . \
      </dev/null >"$dest/$archive"
    printf '%s\t%s\t%s\t%s\t%s\n' "$archive" "$service" "$mount" "$source" "$image" >>"$dest/MANIFEST"
  done
  if [ -n "$stopped" ]; then
    echo "Starting $service"
    docker compose start "$service"
    stopped=""
  fi
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
