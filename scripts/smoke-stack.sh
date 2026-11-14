# This stack's own steps in scripts/smoke-test.sh, which sources this file; it isn't run on its own. It may define
# smoke_prepare, run once the throwaway settings ($work/env) and folders exist, just before the stack starts (it may
# write $work/override-stack.yaml, which is then added to the Compose files), and smoke_check, run once every service
# is healthy and again after the backup has been restored. Both run with smoke-test.sh's variables and functions
# (work, project, root, busybox, compose, fail). Written for bash 3.2 and later (macOS ships 3.2).
# shellcheck shell=bash disable=SC2154 # work, project and busybox come from smoke-test.sh

smoke_prepare() {
  dozzle_prepare
  mealie_prepare
  spoolman_prepare
  torrent_watch_prepare
}

# Spoolman keeps its SQLite database here, so its password secret is the empty file the installing docs have the user
# write for that case, owned by PUID:PGID with mode 600 as on the host (written as root, in a throwaway container).
spoolman_prepare() {
  docker run --rm --network none -v "$(smoke_config_root):/config-root" "$busybox" sh -c '
    mkdir -p /config-root/spoolman/secrets && : >/config-root/spoolman/secrets/db_password &&
    chown -R "$1:$2" /config-root/spoolman/secrets &&
    chmod 700 /config-root/spoolman/secrets && chmod 600 /config-root/spoolman/secrets/db_password' secret \
    "$(sed -n 's/^PUID=//p' "$work/env")" "$(sed -n 's/^PGID=//p' "$work/env")" </dev/null
}

smoke_check() {
  dozzle_check
  mealie_check
  social_to_mealie_check
  qbittorrent_check
  torrent_watch_check
}

# The throwaway CONFIG_ROOT.
smoke_config_root() { sed -n 's/^CONFIG_ROOT=//p' "$work/env"; }

# The Dozzle agent: a throwaway certificate and key, made with `dozzle generate-certs` as on the host and owned by root
# as there; then that the agent loaded them, accepts a hub presenting them and refuses one presenting the pair built
# into every Dozzle image, and that it keeps the notification settings such a hub sends it as it starts.
dozzle_service=dozzle-agent

# The Dozzle image compose.yaml pins.
dozzle_image() { compose config --images "$dozzle_service"; }

# The folder the agent's secrets come from: CONFIG_ROOT/dozzle/certs.
dozzle_certs() { echo "$(smoke_config_root)/dozzle/certs"; }

dozzle_prepare() {
  local certs
  certs=$(dozzle_certs)
  # CONFIG_ROOT belongs to PUID, who may not be the caller: the folder is made as root, inside CONFIG_ROOT (a missing
  # folder Docker Desktop creates for a mount would be in its VM, not on the Mac). generate-certs writes the key with
  # mode 0600 and the certificate with 0644, both owned by root (the user it runs as), as on the host.
  docker run --rm --network none -v "${certs%/dozzle/certs}:/config-root" "$busybox" \
    mkdir -p /config-root/dozzle/certs </dev/null
  docker run --rm --network none -v "$certs:/out" "$(dozzle_image)" --no-analytics generate-certs \
    --cert-out /out/dozzle_cert.pem --key-out /out/dozzle_key.pem </dev/null >/dev/null
}

# `dozzle agent-test` against the agent, as a hub on the stack's network would connect; with -v and -e options for
# docker run as arguments, the hub's certificate and key.
dozzle_agent_test() {
  docker run --rm --network "${project}_default" "$@" "$(dozzle_image)" --no-analytics agent-test \
    "$dozzle_service:7007" </dev/null 2>&1
}

# The agent's log lines about notification settings from a hub, one per line.
dozzle_settings_log() { compose logs --no-color "$dozzle_service" | grep "notification config" || true; }

dozzle_check() {
  local logs out before
  logs=$(compose logs --no-color "$dozzle_service")
  [[ $logs == *"Loaded custom dozzle certificate and key"* ]] ||
    fail "the Dozzle agent didn't load its certificate and key"
  [[ $logs != *"built-in certificate"* ]] || fail "the Dozzle agent uses the built-in certificate"
  out=$(dozzle_agent_test -v "$(dozzle_certs):/certs:ro" -e DOZZLE_CERT=/certs/dozzle_cert.pem \
    -e DOZZLE_KEY=/certs/dozzle_key.pem) || fail "the Dozzle agent refused a hub with its certificate and key: $out"
  out=$(dozzle_agent_test) && fail "the Dozzle agent accepted a hub with the built-in certificate"
  [[ $out == *"certificate"* ]] || fail "unexpected error from a hub with the built-in certificate: $out"
  # A hub with the pair (and no Docker of its own) sends the agent its notification settings as it starts, and the
  # agent saves them to /data: that must not be its read-only root. If the test stops while the hub runs, the EXIT
  # trap removes the hub before the rest.
  dozzle_hub=${project}-dozzle-hub
  before=$(dozzle_settings_log | wc -l)
  trap 'status=$?; docker rm -f "$dozzle_hub" >/dev/null 2>&1; (exit "$status"); cleanup' EXIT
  docker run --detach --name "$dozzle_hub" --network "${project}_default" -v "$(dozzle_certs):/certs:ro" \
    -e DOZZLE_CERT=/certs/dozzle_cert.pem -e DOZZLE_KEY=/certs/dozzle_key.pem \
    -e DOZZLE_REMOTE_AGENT="$dozzle_service:7007" "$(dozzle_image)" --no-analytics </dev/null >/dev/null
  for _ in $(seq 1 30); do
    out=$(dozzle_settings_log | tail -n "+$((before + 1))")
    [ -z "$out" ] || break
    sleep 1
  done
  docker rm -f "$dozzle_hub" >/dev/null
  trap cleanup EXIT
  [[ $out == *"Updated notification config from main server"* ]] ||
    fail "the Dozzle agent didn't take the notification settings a hub sent it: ${out:-it logged nothing}"
  [[ $(compose logs --no-color "$dozzle_service") != *"read-only file system"* ]] ||
    fail "the Dozzle agent tried to write to its read-only root filesystem"
  echo "Dozzle agent: accepts a hub with its own certificate and key and keeps its notification settings, refuses" \
    "one with the built-in pair"
}

# Mealie: a throwaway PostgreSQL in the stack stands in for the database server, with TLS (the image's own
# self-signed certificate) and a pg_hba.conf that refuses every connection over the network without it, and the secret
# with its URL is written as the installing docs have the user write it; then that Mealie keeps its data in that
# database, with no SQLite file of its own, and never writes to its read-only root filesystem.
mealie_service=mealie
mealie_db=mealie-test-db

# The PostgreSQL the database server runs, pinned like busybox in lib.sh (bumped by hand, docs/dependencies.md).
mealie_db_image=postgres:18.7-trixie@sha256:9f4fd4aa918f52ae3d6f3b687f1de90d8f867cd4d520021545a9a50c120eae3b

mealie_prepare() {
  local password
  password=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
  # The secret belongs to PUID:PGID with mode 600, as on the host, so it is written as root, in a throwaway container.
  docker run --rm --network none -v "$(smoke_config_root):/config-root" "$busybox" sh -c '
    mkdir -p /config-root/mealie/secrets &&
    printf "%s\n" "$1" >/config-root/mealie/secrets/database_url &&
    chown -R "$2:$3" /config-root/mealie/secrets &&
    chmod 700 /config-root/mealie/secrets && chmod 600 /config-root/mealie/secrets/database_url' secret \
    "postgresql://mealie:$password@$mealie_db:5432/mealie?sslmode=require" \
    "$(sed -n 's/^PUID=//p' "$work/env")" "$(sed -n 's/^PGID=//p' "$work/env")" </dev/null
  # The socket for the health check and psql; over the network, Mealie's login with TLS only.
  mkdir -p "$work/mealie-test-db"
  printf '%s\n' 'local all all trust' 'hostssl mealie mealie all scram-sha-256' 'host all all all reject' \
    >"$work/mealie-test-db/pg_hba.conf"
  chmod 644 "$work/mealie-test-db/pg_hba.conf"
  # Its data in memory: it holds nothing a backup should keep, and nothing to restore.
  cat >"$work/override-stack.yaml" <<YAML
services:
  $mealie_db:
    image: $mealie_db_image
    user: postgres
    read_only: true
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    tmpfs: [/var/lib/postgresql, /var/run/postgresql, /tmp]
    environment:
      POSTGRES_USER: mealie
      POSTGRES_DB: mealie
      POSTGRES_PASSWORD: $password
    command:
      - postgres
      - -c
      - ssl=on
      - -c
      - ssl_cert_file=/etc/ssl/certs/ssl-cert-snakeoil.pem
      - -c
      - ssl_key_file=/etc/ssl/private/ssl-cert-snakeoil.key
      - -c
      - hba_file=/etc/smoke/pg_hba.conf
    volumes:
      - $work/mealie-test-db:/etc/smoke:ro
    healthcheck:
      test: ["CMD", "pg_isready", "-h", "/var/run/postgresql", "-U", "mealie", "-d", "mealie"]
      interval: 2s
      retries: 30
  $mealie_service:
    depends_on:
      $mealie_db:
        condition: service_healthy
YAML
}

mealie_check() {
  local tables
  # Its tables are there only if it connected, which its database allows only with TLS.
  tables=$(compose exec -T "$mealie_db" psql -U mealie -d mealie -Atc \
    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
  [ "${tables:-0}" -gt 0 ] || fail "Mealie made no tables in its database"
  docker run --rm --network none --volumes-from "$(compose ps --quiet "$mealie_service"):ro" "$busybox" \
    test ! -e /app/data/mealie.db </dev/null || fail "Mealie made a SQLite database of its own"
  [[ $(compose logs --no-color "$mealie_service") != *"Read-only file system"* ]] ||
    fail "Mealie tried to write to its read-only root filesystem"
  echo "Mealie: keeps its data in PostgreSQL ($tables tables), over TLS, with no SQLite file and a read-only root"
}

# social-to-mealie (no keys here, so no import runs): that it keeps the yt-dlp built into its image instead of fetching
# one from PyPI, refuses anything but an http(s) URL before its downloaders run (the check 1.8.2 added), reaches Mealie
# in the stack by its service name, and never writes to its read-only root filesystem.
stm_service=social-to-mealie

# Runs a script with node in the social-to-mealie container (its image has neither curl nor wget) and prints its output.
stm_node() { compose exec -T "$stm_service" node -e "$1" </dev/null; }

social_to_mealie_check() {
  local logs out
  logs=$(compose logs --no-color "$stm_service")
  [[ $logs == *"yt-dlp update skipped"* ]] || fail "social-to-mealie didn't keep the yt-dlp built into its image"
  out=$(stm_node 'fetch("http://127.0.0.1:3000/api/get-url", {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({url: "not a url", tags: []})})
    .then((r) => r.text()).then(console.log)')
  [[ $out == *"valid http(s) URL"* ]] || fail "social-to-mealie didn't refuse a URL that isn't http(s): $out"
  out=$(stm_node 'fetch("http://mealie:9000/api/app/about").then((r) => console.log(r.status))')
  [ "$out" = 200 ] || fail "social-to-mealie doesn't reach Mealie at http://mealie:9000: ${out:-no answer}"
  [[ $logs != *"ead-only file system"* && $logs != *EROFS* ]] ||
    fail "social-to-mealie tried to write to its read-only root filesystem"
  echo "social-to-mealie: keeps its own yt-dlp, refuses non-http(s) URLs, reaches Mealie, and has a read-only root"
}

# qBittorrent, signed in with the temporary password it logs at each start: that its web UI offers the torrent creator
# and the magnet link handler, that it accepts a magnet link, and that it makes a torrent from a folder and seeds it.
# Everything it adds is removed again, because this check also runs after the restore.
qbt_service=qbittorrent-direct
qbt_url=http://127.0.0.1:8091
qbt_magnet_hash=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

# curl in the container, keeping the session in its /tmp.
qbt_api() { compose exec -T "$qbt_service" curl -sS -b /tmp/smoke-session -c /tmp/smoke-session "$@" </dev/null; }

qbittorrent_check() {
  local password page task out
  password=$(compose logs --no-color "$qbt_service" |
    sed -n 's/.*temporary password is provided for this session: //p' | tail -n 1)
  [ -n "$password" ] || fail "qBittorrent logged no temporary password"
  qbt_api --data-urlencode username=admin --data-urlencode "password=$password" "$qbt_url/api/v2/auth/login" >/dev/null
  out=$(qbt_api "$qbt_url/api/v2/app/version")
  [[ $out == v* ]] || fail "qBittorrent refused the login: $out"
  page=$(qbt_api "$qbt_url/")
  [[ $page == *registerMagnetHandler* && $page == *torrentCreator* ]] ||
    fail "qBittorrent's web UI doesn't offer the magnet link handler and the torrent creator"

  qbt_api --data-urlencode "urls=magnet:?xt=urn:btih:$qbt_magnet_hash&dn=smoke-magnet" \
    "$qbt_url/api/v2/torrents/add" >/dev/null
  out=$(qbt_api --get -d "hashes=$qbt_magnet_hash" "$qbt_url/api/v2/torrents/info")
  [[ $out == *"$qbt_magnet_hash"* ]] || fail "qBittorrent didn't take a magnet link: $out"

  compose exec -T "$qbt_service" sh -c \
    'mkdir -p /downloads/smoke-model && head -c 1048576 /dev/urandom >/downloads/smoke-model/weights.bin' </dev/null
  task=$(qbt_api -d sourcePath=/downloads/smoke-model -d format=hybrid -d startSeeding=true \
    "$qbt_url/api/v2/torrentcreator/addTask" | sed -n 's/.*"taskID":"\([^"]*\)".*/\1/p')
  [ -n "$task" ] || fail "qBittorrent didn't start making a torrent"
  for _ in $(seq 1 30); do
    out=$(qbt_api --get -d filter=seeding "$qbt_url/api/v2/torrents/info")
    [[ $out != *'"name":"smoke-model"'* ]] || break
    sleep 1
  done
  [[ $out == *'"name":"smoke-model"'* ]] ||
    fail "qBittorrent isn't seeding the torrent it made: $(qbt_api --get -d "taskID=$task" \
      "$qbt_url/api/v2/torrentcreator/status")"

  qbt_api -d hashes=all -d deleteFiles=true "$qbt_url/api/v2/torrents/delete" >/dev/null
  for _ in $(seq 1 30); do
    out=$(qbt_api "$qbt_url/api/v2/torrents/info")
    [ "$out" != "[]" ] || break
    sleep 1
  done
  [ "$out" = "[]" ] || fail "qBittorrent didn't remove its test torrents: $out"
  compose exec -T "$qbt_service" rm -rf /downloads/smoke-model /tmp/smoke-session </dev/null
  echo "qBittorrent: signs in, takes a magnet link, and makes and seeds a torrent"
}

# torrent-watch: a throwaway password file, as on the host (with no watch terms the service itself never uses it). Then
# the script's real poll, in its container, against this stack's qBittorrent signed in with the temporary password, with
# a stand-in for the index: that it records what a term matches at first, adds what comes later under its own category
# and folder, leaves a torrent with the wrong licence out, and switches a finished torrent outside its category to
# forced seeding, but not one still fetching its metadata. Everything it adds is removed again, as above.
tw_service=torrent-watch
tw_added=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb

torrent_watch_prepare() {
  docker run --rm --network none -v "$(smoke_config_root):/config-root" "$busybox" sh -c '
    mkdir -p /config-root/torrent-watch/secrets &&
    od -An -tx1 -N16 /dev/urandom | tr -d " \n" >/config-root/torrent-watch/secrets/qbittorrent_password &&
    chown -R "$1:$2" /config-root/torrent-watch/secrets &&
    chmod 700 /config-root/torrent-watch/secrets && chmod 600 /config-root/torrent-watch/secrets/qbittorrent_password
  ' secret "$(sed -n 's/^PUID=//p' "$work/env")" "$(sed -n 's/^PGID=//p' "$work/env")" </dev/null
}

torrent_watch_check() {
  local logs password out forced
  logs=$(compose logs --no-color "$tw_service")
  [[ $logs == *"no watch terms set"* ]] || fail "torrent-watch didn't start idle: $logs"
  password=$(compose logs --no-color "$qbt_service" |
    sed -n 's/.*temporary password is provided for this session: //p' | tail -n 1)
  [ -n "$password" ] || fail "qBittorrent logged no temporary password"
  # A finished torrent outside its category, made as in qbittorrent_check, for the poll to switch to forced seeding.
  qbt_api --data-urlencode username=admin --data-urlencode "password=$password" "$qbt_url/api/v2/auth/login" >/dev/null
  compose exec -T "$qbt_service" sh -c \
    'mkdir -p /downloads/smoke-seed && head -c 1048576 /dev/urandom >/downloads/smoke-seed/weights.bin' </dev/null
  qbt_api -d sourcePath=/downloads/smoke-seed -d format=hybrid -d startSeeding=true \
    "$qbt_url/api/v2/torrentcreator/addTask" >/dev/null
  for _ in $(seq 1 30); do
    out=$(qbt_api --get -d filter=seeding "$qbt_url/api/v2/torrents/info")
    [[ $out != *'"name":"smoke-seed"'* ]] || break
    sleep 1
  done
  [[ $out == *'"name":"smoke-seed"'* ]] || fail "qBittorrent isn't seeding torrent-watch's test torrent: $out"
  out=$(compose exec -T -e "SMOKE_PASSWORD=$password" -e "SMOKE_ADDED=$tw_added" "$tw_service" \
    python -I - 2>&1 <<'PYTHON'
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, "/app")
import torrent_watch as tw

password = pathlib.Path("/tmp/smoke-password")
password.write_text(os.environ["SMOKE_PASSWORD"])
state_file = pathlib.Path("/tmp/smoke-state.json")
state_file.unlink(missing_ok=True)
added = os.environ["SMOKE_ADDED"]
listing = [f"a/first  1 GB  1 seeds  magnet:?xt=urn:btih:{'c' * 40}"]
models = {"a/added": "mit", "a/refused": "other"}


def get(url):
    if "/s/" in url:
        return "\n".join(listing) + "\n"
    repo = url.split("=", 1)[1].replace("%2F", "/")
    return json.dumps({"models": [{"id": repo, "license": models[repo], "sizeBytes": 1000}]})


config = tw.load_config(
    {
        "TORRENT_WATCH_TERMS": "smoke",
        "TORRENT_WATCH_INDEX_URL": "https://index.invalid",
        "TORRENT_WATCH_QBITTORRENT_PASSWORD_FILE": str(password),
        "TORRENT_WATCH_STATE_FILE": str(state_file),
        "TORRENT_WATCH_MIN_FREE_GB": "1",
    }
)


def poll():
    client = tw.Qbittorrent(config.qbittorrent_url, config.username, config.password_file)
    tw.Watch(config, tw.Index(config.index_url, get), client, tw.load_state(state_file)).run()


# qBittorrent reports the free space a moment after it starts.
for _ in range(30):
    try:
        poll()
        break
    except tw.WatchError as err:
        print(err)
        time.sleep(2)
else:
    sys.exit("the first poll never worked")
listing += [
    f"a/added  1 GB  1 seeds  magnet:?xt=urn:btih:{added}&dn=smoke-watch",
    f"a/refused  1 GB  1 seeds  magnet:?xt=urn:btih:{'d' * 40}&dn=smoke-refused",
]
poll()
state = json.loads(state_file.read_text())
print(sorted(key for key, value in state["done"].items() if value["why"] == "added"))
password.unlink()
state_file.unlink()
PYTHON
  ) || fail "torrent-watch's poll failed: $out"
  [[ $out == *"'$tw_added'"* ]] || fail "torrent-watch didn't add the new torrent: $out"
  qbt_api --data-urlencode username=admin --data-urlencode "password=$password" "$qbt_url/api/v2/auth/login" >/dev/null
  out=$(qbt_api "$qbt_url/api/v2/torrents/info")
  [[ $out == *"\"hash\":\"$tw_added\""* ]] || fail "qBittorrent doesn't hold torrent-watch's torrent: $out"
  [[ $out == *'"category":"watch"'* && $out == *'"save_path":"/downloads/watch"'* ]] ||
    fail "torrent-watch's torrent isn't under its category and folder: $out"
  [[ $out != *cccccccc* && $out != *dddddddd* ]] ||
    fail "torrent-watch added a torrent it should have left out: $out"
  forced='"name":"smoke-seed"[^}]*"state":"forcedUP"'
  for _ in $(seq 1 15); do
    out=$(qbt_api "$qbt_url/api/v2/torrents/info")
    [[ ! $out =~ $forced ]] || break
    sleep 1
  done
  [[ $out =~ $forced ]] || fail "torrent-watch didn't switch a finished torrent to forced seeding: $out"
  [ "$(printf '%s' "$out" | grep -o forcedUP | wc -l)" -eq 1 ] ||
    fail "torrent-watch forced a torrent that hasn't finished: $out"
  qbt_api -d hashes=all -d deleteFiles=true "$qbt_url/api/v2/torrents/delete" >/dev/null
  for _ in $(seq 1 30); do
    out=$(qbt_api "$qbt_url/api/v2/torrents/info")
    [ "$out" != "[]" ] || break
    sleep 1
  done
  [ "$out" = "[]" ] || fail "qBittorrent didn't remove torrent-watch's test torrent: $out"
  compose exec -T "$qbt_service" rm -rf /downloads/watch /downloads/smoke-seed /tmp/smoke-session </dev/null
  [[ $(compose logs --no-color "$tw_service") != *"Read-only file system"* ]] ||
    fail "torrent-watch tried to write to its read-only root filesystem"
  echo "torrent-watch: starts idle; signs in to qBittorrent, adds only what's new and allowed, under its own category," \
    "and switches what has finished to forced seeding"
}
