#!/usr/bin/env bash
# The unattended backup: take a backup with scripts/backup.sh, copy it off the host with rsync, keep only the newest
# few here and there, and report the result to an Uptime Kuma push monitor. The systemd timer in deploy/systemd/
# runs it nightly (docs/installing.md#scheduled-backups); it can also be run by hand.
#
#   scripts/scheduled-backup.sh
#
# Settings come from backup.env next to compose.yaml (template: backup.env.example; BACKUP_ENV_FILE names another
# file), all optional:
#   BACKUP_DIR          where backups are written (default: backups in the checkout)
#   BACKUP_KEEP         how many to keep in BACKUP_DIR (default: 3)
#   BACKUP_REMOTE       rsync destination, such as an SSH alias and directory (backup-host:/volume1/deimos-backups);
#                       empty = no copy off the host
#   BACKUP_REMOTE_KEEP  how many to keep at BACKUP_REMOTE (default: 30)
#   BACKUP_AGE_RECIPIENT  an age public key (age1...): the copy off the host is then one archive of the backup,
#                       encrypted to that key (backup.tar.age, with its SHA256SUMS), and only the key's owner can read
#                       it; empty = the backup is copied as it is
#   BACKUP_PING_URL     an Uptime Kuma push URL, told "up" after a good run and "down" after a failed one
#
# Only directories named like a backup (<date>-<time>) are ever pruned, here or at BACKUP_REMOTE. A copy is complete
# once its SHA256SUMS has arrived, which is sent last; scripts/restore.sh refuses a directory without one.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=scripts/lib.sh
. "$root/scripts/lib.sh"
cd "$root"
name_re='^[0-9]{8}-[0-9]{6}$'

BACKUP_DIR=backups
BACKUP_KEEP=3
BACKUP_REMOTE=
BACKUP_REMOTE_KEEP=30
BACKUP_PING_URL=
BACKUP_AGE_RECIPIENT=
env_file=${BACKUP_ENV_FILE:-$root/backup.env}
if [ -f "$env_file" ]; then
  # Read as data, never sourced: only these keys, one KEY=value per line.
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in '' | '#'*) continue ;; esac
    key=${line%%=*}
    value=${line#*=}
    value=${value#\"}
    value=${value%\"}
    case $key in
      BACKUP_DIR | BACKUP_KEEP | BACKUP_REMOTE | BACKUP_REMOTE_KEEP | BACKUP_PING_URL | BACKUP_AGE_RECIPIENT)
        printf -v "$key" '%s' "$value" ;;
      *)
        echo "error: unknown setting '$key' in $env_file" >&2
        exit 1
        ;;
    esac
  done <"$env_file"
fi
for key in BACKUP_KEEP BACKUP_REMOTE_KEEP; do
  if ! [[ ${!key} =~ ^[1-9][0-9]*$ ]]; then
    echo "error: $key must be a whole number of 1 or more, not '${!key}'" >&2
    exit 1
  fi
done
if [ -n "$BACKUP_AGE_RECIPIENT" ]; then
  if ! [[ $BACKUP_AGE_RECIPIENT =~ ^age1[02-9ac-hj-np-z]{58}$ ]]; then
    echo "error: BACKUP_AGE_RECIPIENT must be an age public key (age1 and 58 more characters)" >&2
    exit 1
  fi
  if ! command -v age >/dev/null; then
    echo "error: BACKUP_AGE_RECIPIENT is set but age isn't installed (docs/installing.md#scheduled-backups)" >&2
    exit 1
  fi
fi

# Tell the push monitor how the run went; a monitor that hears nothing alerts on its own, so a failed ping is only
# a warning. The URL Uptime Kuma shows may already carry a query string; it is replaced. Retried for two minutes:
# Uptime Kuma runs on another host, whose own nightly backup restarts it, and until it answers again the proxy in
# front of it returns 502.
ping_monitor() {
  if [ -z "$BACKUP_PING_URL" ]; then return 0; fi
  curl --fail --silent --show-error --max-time 10 --retry 12 --retry-delay 10 --retry-all-errors --get \
    --data-urlencode "status=$1" --data-urlencode "msg=$2" "${BACKUP_PING_URL%%\?*}" >/dev/null ||
    echo "warning: couldn't reach the push monitor" >&2
}
report() {
  status=$?
  if [ "$status" -ne 0 ]; then ping_monitor down "backup failed (exit $status), see the journal"; fi
  exit "$status"
}
trap report EXIT

# The names of the backups to delete, from a list of names on stdin: all but the newest $1. Names sort by time.
outdated() {
  local names=() name i
  while IFS= read -r name; do
    if [[ $name =~ $name_re ]]; then names+=("$name"); fi
  done < <(sort)
  for ((i = 0; i < ${#names[@]} - $1; i++)); do echo "${names[$i]}"; done
}

name=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"
"$root/scripts/backup.sh" "$BACKUP_DIR/$name"
size=$(du -sh "$BACKUP_DIR/$name" | cut -f1 | tr -d ' ')

if [ -n "$BACKUP_REMOTE" ]; then
  remote=${BACKUP_REMOTE%/}
  source_dir=$BACKUP_DIR/$name
  if [ -n "$BACKUP_AGE_RECIPIENT" ]; then
    # The whole backup in one archive, encrypted to the recipient before it leaves the host; the host can write it
    # but not read it back. Decrypted (age -d -i <key> backup.tar.age | tar -x), it is the backup as written here.
    sealed=$(mktemp -d)
    # Removes the encrypted copy here, keeping the exit status for report.
    unseal() {
      local status=$?
      rm -rf "$sealed"
      return "$status"
    }
    trap 'unseal; report' EXIT
    mkdir -m 700 "$sealed/$name"
    (umask 077 && tar -C "$BACKUP_DIR" -cf - "$name" | age -r "$BACKUP_AGE_RECIPIENT" -o "$sealed/$name/backup.tar.age")
    (cd "$sealed/$name" && sha256 backup.tar.age >SHA256SUMS && chmod 600 SHA256SUMS)
    source_dir=$sealed/$name
    echo "Encrypted $name for the copy off the host"
  fi
  echo "Copying $name to $remote"
  # -rlpt keeps the modes (600 files, 700 directories) but not owners. SHA256SUMS goes last, marking the copy complete.
  rsync -rlpt --exclude=/SHA256SUMS "$source_dir/" "$remote/$name/"
  rsync -rlpt "$source_dir/SHA256SUMS" "$remote/$name/"

  old=$(rsync --list-only "$remote/" | awk '{print $NF}' | outdated "$BACKUP_REMOTE_KEEP")
  if [ -n "$old" ]; then
    # Delete exactly those directories at the remote: sync an empty directory over it with only them included.
    filters=()
    while IFS= read -r dir; do filters+=("--include=/$dir/***"); done <<<"$old"
    empty=$(mktemp -d)
    echo "Removing $(echo "$old" | wc -l | tr -d ' ') old backup(s) from $remote"
    rsync -r --delete "${filters[@]}" --exclude='*' "$empty/" "$remote/"
    rmdir "$empty"
  fi
fi

old=$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | outdated "$BACKUP_KEEP")
if [ -n "$old" ]; then
  echo "Removing $(echo "$old" | wc -l | tr -d ' ') old backup(s) from $BACKUP_DIR"
  while IFS= read -r dir; do rm -rf "${BACKUP_DIR:?}/$dir"; done <<<"$old"
fi

ping_monitor up "backup $name, $size${BACKUP_REMOTE:+, copied off the host}${BACKUP_AGE_RECIPIENT:+ encrypted}"
echo "Done: $name ($size)"
