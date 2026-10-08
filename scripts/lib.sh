# Helpers shared by backup.sh, restore.sh and smoke-test.sh, which source this file; it isn't run on its own.
# Written for bash 3.2 and later (macOS ships 3.2).
# shellcheck shell=bash

# Reads and writes the services' data in throwaway containers; pinned like every other image (bumped by hand, see
# docs/dependencies.md).
# shellcheck disable=SC2034 # used by the scripts that source this file
busybox=busybox:1.38.0@sha256:fd7dc98638c8e305f4dc34e979f1c0fdfdcaeb0fbf8fcff77ae834b6da3d7e6e

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# A service label listing container paths (comma-separated, exact) that a backup never archives and a restore never
# writes: data far too large to back up here, such as the media library, or disposable, such as a cache or logs.
# shellcheck disable=SC2034 # used by the scripts that source this file
skip_label=org.honeybeartech.deimos.backup.skip

# The container paths container $1 excludes from backups (its skip_label), one per line.
skipped_mounts() {
  docker inspect --format "{{index .Config.Labels \"$skip_label\"}}" "$1" |
    tr ',' '\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | grep -v '^$' || true
}

# The data mounts of container $1, one "<container path><TAB><volume:NAME or host path>" per line: every read-write
# volume or bind mount, except anonymous volumes (an image's own, holding nothing the stack set up), the Docker
# socket and the paths the service's skip_label excludes. These are what a backup archives and a restore replaces.
data_mounts() {
  local dest source skipped
  skipped=" $(skipped_mounts "$1" | tr '\n' ' ') "
  docker inspect --format '{{range .Mounts}}{{if and .RW (or (eq .Type "volume") (eq .Type "bind"))}}'\
'{{.Destination}}{{"\t"}}{{if .Name}}volume:{{.Name}}{{else}}{{.Source}}{{end}}{{"\n"}}{{end}}{{end}}' "$1" |
    while IFS=$'\t' read -r dest source; do
      if [ -z "$dest" ] || [[ "$source" =~ ^volume:[0-9a-f]{64}$ ]]; then continue; fi
      if [[ "$dest" == *docker.sock || "$source" == *docker.sock ]]; then continue; fi
      if [[ "$skipped" == *" $dest "* ]]; then continue; fi
      printf '%s\t%s\n' "$dest" "$source"
    done
}

# Whether $2 is a directory in container $1 (a bind-mounted file isn't archived).
is_dir() { docker run --rm --network none --volumes-from "$1:ro" "$busybox" test -d "$2" </dev/null; }

# The archive name for service $1's mount at $2: <service>--<container path, / as _>.tar.gz
archive_name() {
  local path=${2#/}
  printf '%s--%s.tar.gz' "$1" "${path//\//_}"
}
