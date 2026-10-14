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

# A service label listing paths inside its data mounts (comma-separated, exact container paths) that a backup leaves
# out of the mount's archive and a restore leaves in place: data the service re-creates or doesn't need back, too
# large to copy every night, such as a service's logs.
# shellcheck disable=SC2034 # used by the scripts that source this file
exclude_label=org.honeybeartech.deimos.backup.exclude

# The paths label $2 of container $1 lists, one per line.
label_paths() {
  docker inspect --format "{{index .Config.Labels \"$2\"}}" "$1" |
    tr ',' '\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | grep -v '^$' || true
}

# The container paths container $1 excludes from backups (its skip_label), one per line.
skipped_mounts() { label_paths "$1" "$skip_label"; }

# The data mounts of container $1, one "<container path><TAB><volume:NAME or host path>" per line: every read-write
# volume or bind mount, except anonymous volumes (an image's own, holding nothing the stack set up), the Docker
# socket and the paths the service's skip_label excludes. These are what a backup archives and a restore replaces.
data_mounts() {
  local dest source skipped
  skipped=$(skipped_mounts "$1")
  docker inspect --format '{{range .Mounts}}{{if and .RW (or (eq .Type "volume") (eq .Type "bind"))}}'\
'{{.Destination}}{{"\t"}}{{if .Name}}volume:{{.Name}}{{else}}{{.Source}}{{end}}{{"\n"}}{{end}}{{end}}' "$1" |
    while IFS=$'\t' read -r dest source; do
      if [ -z "$dest" ] || [[ "$source" =~ ^volume:[0-9a-f]{64}$ ]]; then continue; fi
      if [[ "$dest" == *docker.sock || "$source" == *docker.sock ]]; then continue; fi
      # Paths may contain spaces, so the skip list is compared line by line.
      if [ -n "$skipped" ] && grep -qxF -- "$dest" <<<"$skipped"; then continue; fi
      printf '%s\t%s\n' "$dest" "$source"
    done
}

# Fails unless every path container $1's exclude_label lists is an absolute path strictly inside one of its data
# mounts, with no pattern characters or `..` (tar and find read them as patterns), so a typo can't quietly change
# what is backed up.
check_excludes() {
  local path mount mounts inside
  mounts=$(data_mounts "$1" | cut -f1)
  while IFS= read -r path; do
    inside=false
    while IFS= read -r mount; do
      if [ -n "$mount" ] && [[ "$path" == "$mount"/?* ]]; then inside=true; fi
    done <<<"$mounts"
    if ! $inside || [[ "$path" == *..* || "$path" == *[\*\?\[\\]* ]]; then
      echo "error: $exclude_label: $path isn't a plain path inside one of the service's data mounts" >&2
      return 1
    fi
  done < <(label_paths "$1" "$exclude_label")
}

# The mounts of container $1 that lie inside its mount at $2 (any other mount at a path below it), one container
# path per line. Each is backed up on its own, or skipped; a backup of $2 leaves them out, and a restore of $2
# leaves them and the directories leading to them in place.
nested_mounts() {
  local dest
  docker inspect --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' "$1" |
    while IFS= read -r dest; do
      if [ -n "$dest" ] && [[ "$dest" == "$2"/* ]]; then printf '%s\n' "$dest"; fi
    done
}

# The paths inside container $1's mount at $2 that the mount's archive leaves out and a restore of it spares, one per
# line: the mounts below it (nested_mounts) and the paths its exclude_label lists there.
left_out() {
  local path
  { nested_mounts "$1" "$2" && label_paths "$1" "$exclude_label"; } |
    while IFS= read -r path; do
      if [[ "$path" == "$2"/* ]]; then printf '%s\n' "$path"; fi
    done | sort -u
}

# Whether $2 is a directory in container $1 (a bind-mounted file isn't archived).
is_dir() { docker run --rm --network none --volumes-from "$1:ro" "$busybox" test -d "$2" </dev/null; }

# The archive name for service $1's mount at $2: <service>--<container path, / as _>.tar.gz
archive_name() {
  local path=${2#/}
  printf '%s--%s.tar.gz' "$1" "${path//\//_}"
}

# The stack's name in homelab-ops' pull agent (homelab-pull), which names its state directory, and how long (seconds)
# deploy_lock waits for a deploy in progress. deploy_lock is the same text in every homelab stack; only pull_stack
# differs.
pull_stack=deimos
deploy_lock_timeout=1800

# Waits until the pull agent isn't deploying this stack, then keeps it from starting a deploy until the calling
# script exits: the agent takes the same lock with `flock --nonblock` and skips its run while it's held, so it never
# starts or recreates a service that a backup has stopped or a restore is rewriting, and deploys at its next run
# afterwards. Does nothing where the agent keeps no state for the stack (CI, a workstation, a host without the agent).
# The lock is held on file descriptor 9 until the script exits.
deploy_lock() {
  local dir=${HOMELAB_PULL_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/homelab-pull}/$pull_stack
  if [ ! -d "$dir" ]; then return 0; fi
  if ! command -v flock >/dev/null; then
    echo "error: $dir exists (the pull agent deploys here) but flock isn't installed to lock it" >&2
    return 1
  fi
  exec 9>>"$dir/lock"
  if ! flock --nonblock 9; then
    echo "Waiting for a deploy by the pull agent to finish (up to $deploy_lock_timeout s)"
    if ! flock --timeout "$deploy_lock_timeout" 9; then
      echo "error: the pull agent's deploy of $pull_stack still holds $dir/lock after $deploy_lock_timeout s;" \
        "nothing was stopped (check: journalctl --user -u homelab-pull@$pull_stack)" >&2
      return 1
    fi
  fi
}
