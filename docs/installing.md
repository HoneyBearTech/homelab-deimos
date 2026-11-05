# Installing

The [quick start](quick-start.md) is the short version of this page.

## Requirements

- Linux with Docker Engine and the Compose v2 plugin (Docker Engine 25 or later and Compose 2.24 or later, for
  the health checks' `start_interval`; **Docker Engine 29.2 or later on a host with a GPU**, see
  [below](#the-gpu-and-docker-292)). The reference host is **Ubuntu 24.04 on amd64**; every image is chosen to
  publish `linux/amd64`.
- For hardware transcoding: an **NVIDIA GPU** (passed through to the VM if the host is virtual), the NVIDIA
  driver, and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  1.18 or later configured for Docker (`sudo nvidia-ctk runtime configure --runtime=docker`, then restart Docker), and
  `COMPOSE_FILE=compose.yaml:compose.gpu.yaml` in `.env`. Without a GPU, leave that line out: Tdarr still runs
  and transcodes on the CPU.
- A user in the `docker` group to run `docker compose`. Membership is equivalent to root on the host, so keep
  that group small.
- The media library mounted on the host (local disk or a network share) and writable by `PUID`/`PGID`.
- Disk for `CONFIG_ROOT`, which holds Tdarr's transcode cache too: room for several of your largest files at once,
  ideally on fast local disk rather than the network share.
- `age` if the backups are copied off the host encrypted ([Scheduled backups](#scheduled-backups)).

### The GPU and Docker 29.2

From Docker Engine 29.2, Docker serves the GPU reservation in [`compose.gpu.yaml`](../compose.gpu.yaml)
(`driver: nvidia`) through CDI, the Container Device Interface, whenever the NVIDIA Container Toolkit's
`nvidia-cdi-hook` and a CDI spec for the GPU are installed. Toolkit 1.18 and later ship both, and its
`nvidia-cdi-refresh` keeps the spec current at boot and after every driver install or upgrade
(`nvidia-ctk cdi list` shows `nvidia.com/gpu=all`).

An older Engine passes the GPU through the NVIDIA runtime hook instead, and with Docker's systemd cgroup driver (the
default on cgroup v2) the container then **loses the GPU at every `systemctl daemon-reload`**, which apt and
unattended-upgrades run. Tdarr can't use the GPU any more (NVENC/NVDEC fail), and `nvidia-smi` in the container
fails with "Failed to initialize NVML: Unknown Error"
([NVIDIA container-toolkit issue #48](https://github.com/NVIDIA/nvidia-container-toolkit/issues/48)); only
restarting the container brings the GPU back, until the next reload. The compose files are the same either way:
upgrading Docker is the fix (it restarts every container on the host).

To check a GPU host, reload systemd and ask Tdarr for the GPU:

```sh
docker version --format '{{.Server.Version}}'   # 29.2 or later
sudo systemctl daemon-reload
docker exec tdarr nvidia-smi -L                 # still lists the GPU
```

## Where data lives

Every app keeps its data in its own folder under `CONFIG_ROOT`, the one path setting for app data: its settings and
database in `CONFIG_ROOT/<app>/config`, and any other folder it needs beside that, `CONFIG_ROOT/<app>/<purpose>`.
`<app>` is the app's lowercase name.

| What | Where on the host | In the container |
| --- | --- | --- |
| The media library | `MEDIA_PATH` | `/media` |
| qBittorrent's torrents' files (not backed up) | `TORRENTS_PATH` | `/downloads` |
| Tdarr's settings | `CONFIG_ROOT/tdarr/config` | `/app/configs` |
| Tdarr's database (libraries, flows, history) | `CONFIG_ROOT/tdarr/server` | `/app/server` |
| Tdarr's logs (not backed up) | `CONFIG_ROOT/tdarr/logs` | `/app/logs` |
| Tdarr's transcode cache (not backed up) | `CONFIG_ROOT/tdarr/transcode` | `/temp` |
| Spoolman's database | `CONFIG_ROOT/spoolman/config` | `/home/app/.local/share/spoolman` |
| The AMS app's printer list (with the printers' access codes) and settings | `CONFIG_ROOT/haspelsync/config` | `/app/printers` |
| The AMS app's logs (not backed up) | `CONFIG_ROOT/haspelsync/logs` | `/app/logs` |
| Mealie's recipe images, its own backups and the keys it signs sign-ins with | `CONFIG_ROOT/mealie/config` | `/app/data` |
| qBittorrent's settings, login and torrent list | `CONFIG_ROOT/qbittorrent-direct/config` | `/config` |
| torrent-watch's record of what it has seen and added | `CONFIG_ROOT/torrent-watch/config` | `/config` |
| Mealie's database URL (not backed up; [below](#mealies-database)) | `CONFIG_ROOT/mealie/secrets/database_url` | `/run/secrets/mealie_database_url`, read-only |
| Spoolman's database password (not backed up; empty with SQLite; [below](#spoolmans-database)) | `CONFIG_ROOT/spoolman/secrets/db_password` | `/run/secrets/spoolman_db_password`, read-only |
| qBittorrent's password, for torrent-watch (not backed up; [below](#torrent-watch)) | `CONFIG_ROOT/torrent-watch/secrets/qbittorrent_password` | `/run/secrets/qbittorrent_password`, read-only |
| The Dozzle agent's certificate and key (owned by root, not backed up; [below](#dozzles-certificate)) | `CONFIG_ROOT/dozzle/certs` | `/run/secrets/dozzle_cert`, `/run/secrets/dozzle_key`, read-only |

The folders must be readable and writable by `PUID:PGID`. Tdarr's transcode cache needs room for several of your
largest files at once, so `CONFIG_ROOT` should be on a disk with that much space. Every mount is listed in
[interfaces.md](interfaces.md#volumes-and-mounts).

> **Warning: never point `CONFIG_ROOT` (or any other path setting) into `/var/lib/docker/volumes`, and don't keep
> app data in named Docker volumes.** `docker volume prune`, `docker system prune --volumes` and Portainer's
> "remove unused volumes" delete every volume that no container mounts *as a volume*, including one whose `_data`
> folder a service bind-mounts by its path: Docker counts that volume as unused, and the app's data is gone. Keep
> the data under `CONFIG_ROOT`, outside Docker's own directories.

## Installing

1. Clone the repository (or download a release's source archive and verify it,
   [verifying-releases.md](verifying-releases.md)).
2. Create `.env` from `.env.example` (mode `600`) and set every value.
3. Create the folders as your user (so Docker doesn't create them owned by root), then `docker compose up -d`:

   ```sh
   . ./.env && mkdir -p "$CONFIG_ROOT"/tdarr/{config,server,logs,transcode} "$CONFIG_ROOT"/spoolman/config \
     "$CONFIG_ROOT"/haspelsync/{config,logs} "$CONFIG_ROOT"/mealie/config "$CONFIG_ROOT"/qbittorrent-direct/config \
     "$CONFIG_ROOT"/torrent-watch/config "$TORRENTS_PATH"
   ```

   The Dozzle agent also needs its certificate and key in `CONFIG_ROOT/dozzle/certs`, owned by root
   ([below](#dozzles-certificate)), Mealie the URL of its database in `CONFIG_ROOT/mealie/secrets`
   ([below](#mealies-database)), and Spoolman the file for its database password, empty while it keeps SQLite
   ([below](#spoolmans-database)). social-to-mealie needs its API keys in `social-to-mealie.env` before it can import
   anything ([below](#social-to-mealie)), and qBittorrent a password of your own at its first sign-in
   ([below](#qbittorrent)), which torrent-watch needs in a file before its container can be created
   ([below](#torrent-watch)).

**Adopting existing containers.** If the services already run on the host (from Portainer stacks or another
Compose project), move their existing data into the folders above instead of starting empty: stop the old
containers, copy each one's data into its folder with the owners kept (`cp -a`; they should be `PUID:PGID`), and
keep the same published ports and the **same container path for the media library**: Tdarr stores every file's
path in its database, so a different mount point makes it see a new library.
`docker inspect --format '{{json .Mounts}}' tdarr` shows what a container mounts where. Take a backup of the old data
first, and remove the old copies only once the stack runs from `CONFIG_ROOT`.

The maintainer's server runs `main` rather than a release, and deploys every merge by itself
([upgrading.md](upgrading.md#following-main)). For your own setup, a release is the safer choice.

## Dozzle's certificate

The Dozzle agent and the homelab's Dozzle (the hub, on another host) authenticate each other with a certificate and
key: each end presents the pair and accepts only a peer that presents the same one. Every Dozzle image also ships a
built-in pair, the same in every copy, so an agent that uses it accepts anyone on the network who has a Dozzle image,
and through it they can read every container's log and control Docker. This stack never uses the built-in pair: the
agent reads its own from two files under `CONFIG_ROOT/dozzle/certs` (Docker secrets, mounted read-only in
`/run/secrets`), and it doesn't start while either file is missing.

| File | Owner, mode | What |
| --- | --- | --- |
| `CONFIG_ROOT/dozzle/certs/dozzle_cert.pem` | `root:root`, `644` | The certificate (self-signed, valid for five years) |
| `CONFIG_ROOT/dozzle/certs/dozzle_key.pem` | `root:root`, `600` | Its private key: **a secret**; whoever holds it can connect to the agent |

Both belong to root: the agent runs as root without capabilities, so it can read only a file that root owns (or one
that every user can read, which the key must never be).

**One pair for the hub and all its agents.** Make it once, on a machine you trust, with the Dozzle image this stack
pins (the `image:` of `dozzle-agent` in `compose.yaml`):

```sh
mkdir -m 700 dozzle-pair
docker run --rm --network none -v "$PWD/dozzle-pair:/out" amir20/dozzle:<tag>@sha256:<digest> \
  --no-analytics generate-certs --cert-out /out/dozzle_cert.pem --key-out /out/dozzle_key.pem
```

Save both files in a password manager: they are never in the stack's backups (`scripts/backup.sh` copies read-write
mounts only), so that is where a rebuilt host gets them back from. Then copy them to every host that runs the hub or
an agent (for example with `scp` into your home directory) and, in each checkout, put them in place:

```sh
. ./.env
sudo install -d -o root -g root -m 755 "$CONFIG_ROOT/dozzle/certs"
sudo install -o root -g root -m 644 ~/dozzle_cert.pem "$CONFIG_ROOT/dozzle/certs/dozzle_cert.pem"
sudo install -o root -g root -m 600 ~/dozzle_key.pem "$CONFIG_ROOT/dozzle/certs/dozzle_key.pem"
rm ~/dozzle_cert.pem ~/dozzle_key.pem
```

`docker compose logs dozzle-agent` then says `Loaded custom dozzle certificate and key` at each start. The hub and every
agent must hold the same pair: an agent refuses a hub with any other one, and the hub then shows that host as
unavailable. So to change it (before it expires, or at once if the key may have leaked), put the new pair on every
host first, then recreate the hub and each agent with it, one right after the other
(`docker compose up -d --force-recreate dozzle-agent` here): until the last one has it, the hub can't show the hosts whose
pair differs from its own. The logs themselves aren't lost: Docker keeps them, and the hub shows them again once
it reaches the agent.

## Mealie's database

Mealie keeps its recipes, meal plans, shopping lists and accounts in PostgreSQL on a database server, not in this
stack; its folder here holds only the recipe images, its own backups and the keys it signs sign-ins with. It reaches
the server with one URL, login and password included, which it reads from a file (a Docker secret, mounted read-only
at `/run/secrets/mealie_database_url`), so the password is never in `.env` or in the container's settings. Mealie
doesn't start while the file is missing.

1. **On the database server**, make a login that owns one new database, with a long random password (for
   example `openssl rand -hex 24`), as its administrator in `psql`:

   ```sql
   CREATE ROLE mealie LOGIN PASSWORD '<the password>';
   CREATE DATABASE mealie OWNER mealie;
   ```

   and let the login connect from this host (`pg_hba.conf`, with `scram-sha-256`).
2. **On this host**, write the URL into the file, as the user who runs the stack (so it belongs to `PUID:PGID`, whom
   Mealie runs as), with mode `600`. `read -s` keeps the password out of the shell's history:

   ```sh
   . ./.env
   install -d -m 700 "$CONFIG_ROOT/mealie/secrets"
   (umask 077 && read -rsp 'Password: ' pw && echo &&
     printf 'postgresql://mealie:%s@db.example.com:5432/mealie?sslmode=require\n' "$pw" \
       >"$CONFIG_ROOT/mealie/secrets/database_url")
   ```

   Use the database server's name and the login and database from step 1. A password with any of `@:/?#%` in it
   must be percent-encoded in the URL (a hexadecimal one never needs it). `sslmode=require`
   encrypts the connection; `verify-full` would also check the server's certificate, which needs its CA certificate
   inside the container.
3. **Start it**: `docker compose up -d mealie`. On its first start Mealie makes its tables, and its first account:
   open `MEALIE_PUBLIC_URL`, sign in as `changeme@example.com` with the password `MyPassword`, and change both at
   once (Mealie makes that account only while the database has no users).

The file is never in the stack's backups (`scripts/backup.sh` copies read-write mounts only), and the database is
backed up on its server, not here. To give the login a new password (`ALTER ROLE mealie PASSWORD '…'` on the
server), write the file again as in step 2 and `docker compose restart mealie`; on a rebuilt host, write it again
the same way.

## Spoolman's database

Spoolman keeps its database in SQLite in its folder unless `spoolman.env` points it at a PostgreSQL server. Either
way it reads a password file (a Docker secret, mounted read-only at `/run/secrets/spoolman_db_password`), and its
container can't be created while the file is missing. With SQLite, make it empty, as the user who runs the stack:

```sh
. ./.env
install -d -m 700 "$CONFIG_ROOT/spoolman/secrets"
install -m 600 /dev/null "$CONFIG_ROOT/spoolman/secrets/db_password"
```

To keep it on a PostgreSQL server instead:

1. **On the database server**, make a login that owns one new database (`CREATE ROLE spoolman LOGIN PASSWORD
   '<the password>'; CREATE DATABASE spoolman OWNER spoolman;`) and let it connect from this host.
2. **On this host**, write the password into the file with **no line break after it** (Spoolman reads the whole
   file as the password), and create `spoolman.env` from `spoolman.env.example` (mode `600`) with the server's name:

   ```sh
   (umask 077 && read -rsp 'Password: ' pw && echo && printf '%s' "$pw" >"$CONFIG_ROOT/spoolman/secrets/db_password")
   cp spoolman.env.example spoolman.env && chmod 600 spoolman.env   # then set SPOOLMAN_DB_HOST
   ```

3. **Recreate it**: `docker compose up -d spoolman`. On its first start on an empty database Spoolman makes its
   tables.

Spoolman doesn't move its data over. To keep it, stop Spoolman after that first start, copy `spoolman.db` from its
folder, empty every table in the new database (`TRUNCATE … RESTART IDENTITY`; the first start fills
`alembic_version`, which holds the same revision on both engines) and copy the data with pgloader (`data only`,
`quote identifiers`); compare each table's row count before starting it again. Removing `spoolman.env` and
recreating it goes back to the SQLite file, without what was written to PostgreSQL in between. The password file
and the database are never in the stack's backups; back the database up on its server.

## social-to-mealie

social-to-mealie turns a post on Instagram, TikTok, YouTube Shorts and similar sites into a Mealie recipe: it fetches
the post, has OpenAI's API transcribe its audio and write the recipe, and sends that to Mealie inside the stack. It
keeps no data of its own; it needs two keys and the names of two models, which it reads only from its environment,
so they go in `social-to-mealie.env` (gitignored, mode `600`, in the stack's backups like `.env`). Without the file it
starts, but every import fails.

1. **In Mealie**, make a user for it (not an administrator; the recipes it imports belong to that user), sign in as
   that user and create an API token under the user's profile (API tokens).
2. **At OpenAI**, create an API key, ideally in a project of its own with a monthly spending limit: every import
   costs a little, and anyone who can reach social-to-mealie can start one.
3. **On this host**, `cp social-to-mealie.env.example social-to-mealie.env && chmod 600 social-to-mealie.env`, put
   both keys in it, and keep or change the models (a speech-to-text model, and a chat model with structured outputs),
   then `docker compose up -d social-to-mealie`.

It has no login of its own: keep its port (9926) on the LAN, behind the reverse proxy's access list. Browsers install
it as an app (to share links to it from a phone) only over HTTPS, so through the reverse proxy. Use version 1.8.2 or
later: earlier versions let anyone who could reach it run commands in its container (GHSA-r63c-m6m6-mvh3).

## qBittorrent

qBittorrent is a torrent client for files you're free to share. It makes torrents, seeds them, and downloads from
torrent files and magnet links. Only torrent-watch talks to it, and the only files it can reach are in
`TORRENTS_PATH` (mounted at `/downloads`), so keep the media library out of that folder. Every path you set in
qBittorrent must be under `/downloads`: anything else is lost when the container is recreated.

1. **Sign in and set a password.** The user is `admin`, and each start logs a temporary password until you set your
   own: `docker compose logs qbittorrent-direct | grep 'temporary password'`. Open port 8091 and set a long, unique
   password under Tools → Options → WebUI.
2. **Check the address it uses.** Peers and trackers see the public IP address this host reaches the internet from,
   and this stack does nothing to hide it. Turn on Tools → Options → Behavior → Show external IP, and check that the
   status bar shows the address you expect before you add a torrent.
3. **Forward its peer port.** Forward port 50881, TCP and UDP, from the router to this host, so peers can connect to
   it; without that it can still reach out to them, but it seeds to far fewer. Never forward the web UI (8091). Under
   Tools → Options → Speed, set a global upload limit that leaves room for everything else on your connection.
4. **On a network share**, keep Tools → Options → Advanced → Disk IO type at Default (reading and writing the files on
   this build), never memory-mapped files ([dependencies.md](dependencies.md#qbittorrent)).
5. **Behind a reverse proxy**, keep its access list to the LAN, and turn on Tools → Options → WebUI → Enable reverse
   proxy support with the proxy's address in the trusted proxies list. Without that, repeated failed sign-ins ban the
   proxy, and so everyone who uses it.

**To share files**, copy them into `TORRENTS_PATH`, then open Tools → Torrent Creator → Create New Torrent. Type the
source as its path in the container, under `/downloads`, and tick **Start seeding immediately**, which is off by
default. Export Torrent in the creator's list gives you the torrent file, and a torrent's context menu in the main list
has Copy → Magnet link.

**To download**, use File → Add Torrent Link, which takes magnet links as well as URLs of torrent files. To have a
browser open magnet links in it, use Tools → Register to handle magnet links, which browsers allow only over HTTPS, so
through the reverse proxy.

## torrent-watch

torrent-watch adds new torrents from a model index to qBittorrent by itself. Every half hour it runs each of your watch
terms through the index's search and adds what it hasn't seen before, under the category `watch`, in `/downloads/watch`.
What a term matches the first time it runs is only recorded, so only torrents listed after that are added. With no
terms, and no [torrents at risk](#torrent-watch) asked for, it does nothing.

Nobody is asked before a torrent is added, and it seeds from the same public IP address as everything else in
qBittorrent ([above](#qbittorrent)), until you remove it. So a torrent is added only when all of these hold:

- its name doesn't mark it as uncensored, by the words the index's own filter looks for: `uncensored`, `abliterated`,
  `heretic`, `decensored`, `obliteratus` and `obliterated`, anywhere in the name, in any case;
- the index names its licence, and that is MIT, Apache-2.0, WTFPL or OpenMDW-1.1, none of which has terms to accept;
- the index names its size, and that is within `TORRENT_WATCH_MAX_TORRENT_GB`;
- what the category holds in qBittorrent stays within `TORRENT_WATCH_MAX_TOTAL_GB` with it added;
- the disk keeps `TORRENT_WATCH_MIN_FREE_GB` free once this and everything else still downloading has finished.

Anything it can't tell counts as a no, and it looks again at the next poll. It adds at most three torrents per poll. If
the disk falls below `TORRENT_WATCH_MIN_FREE_GB` anyway, it stops its own unfinished downloads, and they stay stopped
until you start them again. A torrent you remove from qBittorrent makes room under the category's limit, and is never
added again.

Once any torrent in qBittorrent has finished, the next poll switches it to forced seeding, so that qBittorrent's queue
limits don't stop it seeding. That covers every torrent there, your own uploads and what you add by hand included, and
what is already seeding at its first poll, though only while it has a watch term to run. It happens once per torrent:
one you switch back or stop stays that way. Stopping for lack of space still touches only its own category. Forced
torrents still count toward a global upload limit, if
you've set one ([above](#qbittorrent)), so that is what leaves room for everything else on your connection.

1. **Give it qBittorrent's password.** Compose can't create the container without this file, so on a new installation
   choose the password now and set the same one at qBittorrent's first sign-in ([above](#qbittorrent)). As the user who
   runs the stack, so that the file belongs to `PUID:PGID`. `read -s` keeps the password out of the shell's history:

   ```sh
   . ./.env
   mkdir -p "$CONFIG_ROOT/torrent-watch/config"
   install -d -m 700 "$CONFIG_ROOT/torrent-watch/secrets"
   (umask 077 && read -rsp 'Password: ' pw && echo &&
     printf '%s\n' "$pw" >"$CONFIG_ROOT/torrent-watch/secrets/qbittorrent_password")
   ```

   It reads the file at every poll, so after changing the password in qBittorrent, write it here too.
2. **Set what to watch and the limits** in `.env` ([interfaces.md](interfaces.md)), then
   `docker compose up -d torrent-watch`. Set `TORRENT_WATCH_MIN_FREE_GB` to suit the disk, above all if the media
   library shares it.
3. **Read its log**: `docker compose logs torrent-watch`. A new term says how many torrents it already matches, and
   each torrent it adds or leaves out gets a line.

**To take what is at risk**, set `TORRENT_WATCH_AT_RISK=true`, with or without watch terms. At risk is the index's own
word: a torrent with no observed seeders whose source is still available. What is at risk now is the point, so nothing
is only recorded first, and it takes the smallest first, to save the most torrents per GB. The rules above still hold, so
expect it to stop at your limits long before it runs out of torrents: the list can come to many terabytes. It asks for
the whole list every 6 hours, about 100 requests over several minutes, and adds from that answer at each poll. The index's
list has ended at different lengths from one run to the next, so what it sees may not be everything at risk. With no
seeders the data comes from the index's web seed, so speeds vary, and a download can stall for as long as that does. One
that never finishes keeps counting toward the limits until you remove it.

**Watch terms** are separated by commas. Words (`qwen gguf`) match any model with those words in its name, whoever
made it; one author with a slash after it (`someone/`) keeps only that author's models. Keep them narrow: the index's
search stops at about 90 rows, best-seeded first, so a new torrent with no seeders can miss a long list. The log warns
about a term that matches 75 or more.

**The index** must answer `GET /s/<words>` (words joined with `+`) with one line per torrent, starting with
`<author>/<model>` and ending with its magnet link, and `GET /api/models?q=<author>/<model>` with JSON whose `models`
give each one's `id`, `license` and `sizeBytes`. For torrents at risk, `GET /api/models?license=<licence>` must list them
page by page (`nextCursor`, `seeded`, `seed`, `hfBacked`), and `GET /api/torrents?repo=<author>/<model>` give `magnet` and
`seeders`. If it stops answering like that, nothing is added, and the log says
why. Its word for a licence is all this has to go on: read the licence before relying on a model.

## Scheduled backups

`scripts/scheduled-backup.sh` takes a backup (`scripts/backup.sh`), copies it to another machine with rsync
(encrypted with age, when `BACKUP_AGE_RECIPIENT` is set), keeps only the newest few in both places and reports to an
Uptime Kuma push monitor. A systemd timer runs it nightly. Every setting is in the optional `backup.env`
([interfaces.md](interfaces.md#backupenv)). The backups hold every login and the printers' access codes: copy them
only to a machine you trust as much as this one, or encrypt the copy (step 3). The media library is never in them;
protect it with snapshots where it is stored.

1. **On the backup server**, create a folder for the backups and a user that can write only there and use rsync
   over SSH (on a Synology: a shared folder, a non-admin user with read/write on that folder only, rsync allowed
   under Application Privileges, SSH on, the rsync service on under File Services, and the user home service on so
   the user can have an `authorized_keys`). Whoever controls this host can delete or overwrite what it copied
   there, so keep older versions where the backup user can't reach them: snapshots of the folder, or a versioned
   copy of it made on the server after the nightly backup (on a Synology without Btrfs, a Hyper Backup task with
   rotation, into a folder the backup user has no access to). A recycle bin isn't enough: Synology's doesn't keep
   files deleted over rsync.
2. **On this host**, as the user who runs the stack, install rsync and curl (and pigz, which compresses the
   archives on every core instead of one; age, to encrypt the copy), and create a key used only for the backups,
   with an alias for it in `~/.ssh/config`:

   ```sh
   sudo apt install -y rsync curl pigz age
   ssh-keygen -t ed25519 -N '' -f ~/.ssh/homelab-deimos-backup
   ```

   ```text
   Host backup-host
     HostName <the backup server's address>
     User <the backup user>
     IdentityFile ~/.ssh/homelab-deimos-backup
     IdentitiesOnly yes
   ```

   Add the public key to the backup user's `~/.ssh/authorized_keys` on the server, prefixed with
   `restrict,from="<this host's address>"` (no shell or forwarding, and only from this host), then check that
   `rsync --list-only backup-host:/volume1/<folder>/` works without a password prompt.
3. **The encryption key** (optional, recommended), on another machine you trust (not this host):
   `age-keygen -o deimos-backup.key`. It prints the public key (`age1...`). Keep the key file where you keep your
   other recovery secrets, such as a password manager, and never copy it to this host: without it no encrypted
   backup off the host can be read, and with it any of them can.
4. **Settings**: `cp backup.env.example backup.env && chmod 600 backup.env`, then set
   `BACKUP_REMOTE=backup-host:/volume1/<folder>`, `BACKUP_AGE_RECIPIENT=age1...` (the public key from step 3; leave
   it empty to copy the backups as they are) and, optionally, the retention and `BACKUP_PING_URL`. For the ping, add
   a monitor of type **Push** in Uptime Kuma with a heartbeat interval of 25 hours, and copy its URL.
5. **Try it**: `scripts/scheduled-backup.sh`. Each service stops only while its own data is copied (compression
   runs after it starts again): Spoolman, the AMS app and Mealie for seconds, Tdarr for as long as its database takes to
   copy, and a transcode it was running starts over. With `BACKUP_AGE_RECIPIENT`, the copy off the host is one
   folder per backup holding only `backup.tar.age` and its `SHA256SUMS`; the backup itself stays readable here, in
   `BACKUP_DIR`.
6. **Schedule it**, with the systemd user units in [`deploy/systemd/`](../deploy/systemd/) (they expect the
   checkout at `~/homelab-deimos`; edit both paths in the `.service` file if it's elsewhere). Pick an hour when
   Tdarr's queue is quiet and nobody prints (`OnCalendar` in the `.timer` file). Lingering lets the timer run while
   you're logged out:

   ```sh
   mkdir -p ~/.config/systemd/user
   cp deploy/systemd/homelab-deimos-backup.* ~/.config/systemd/user/
   sudo loginctl enable-linger "$USER"
   systemctl --user daemon-reload
   systemctl --user enable --now homelab-deimos-backup.timer
   systemctl --user list-timers homelab-deimos-backup.timer   # next run: 04:00, plus up to 10 minutes
   journalctl --user -u homelab-deimos-backup                 # what the last runs did
   ```

Restore a copy from the backup server as [rebuilding.md](rebuilding.md) describes; `scripts/restore.sh` verifies
its `SHA256SUMS` first, and a copy that was cut off has none yet, so it is refused. An encrypted copy is decrypted
first with the private key (`age -d -i deimos-backup.key backup.tar.age | tar -x`), which gives the backup directory
`scripts/restore.sh` reads; check the copy's `SHA256SUMS` before that (it is sent last, so a copy that was cut off
has none; [rebuilding.md](rebuilding.md#an-encrypted-backup)).

## Running it securely

- **Web UIs on the LAN only.** Tdarr, Spoolman, the AMS app and social-to-mealie mostly have no login of their own;
  whoever reaches social-to-mealie spends its OpenAI key and adds recipes to Mealie. Don't publish their ports beyond
  the LAN; Docker-published ports bypass host firewalls such as `ufw`, so restrict them at the
  router or with Docker's own `DOCKER-USER` rules, and use a reverse proxy's access lists for names you give them.
- **qBittorrent is on the public internet** by design: peers see your public IP address. Forward only its peer port
  (50881), never its web UI, and give it only files you're free to share ([qBittorrent](#qbittorrent)).
- **torrent-watch adds torrents without asking**, under that same address. Watch only what you're free to share, and
  set its limits to suit the disk ([torrent-watch](#torrent-watch)).
- **Turn on logins where a service offers them**, with long, unique passwords: HaspelSync's under Settings →
  Network access. Mealie always has one: change its first account's e-mail and password at once
  ([Mealie's database](#mealies-database), step 3).
- **Mealie and social-to-mealie fetch whatever address they are given** to import a recipe, including addresses on
  the LAN: anyone with a Mealie account, and anyone who reaches social-to-mealie, can give them one. New Mealie
  accounts need an administrator's invitation; invite only people you trust.
- **HaspelSync answers only the host names it knows.** Under its IP address it works as is; reached through a
  reverse proxy or under a DNS name, it refuses every request (`403`) until that name is added under Settings →
  Allowed host names. Its API answers only its own web UI and callers with an API key.
- **Protect the media library.** Tdarr replaces files in place. Keep snapshots on the storage that holds the
  library, check new flows on a small library first, and read Tdarr's release notes before upgrading it.
- **The GPU through the NVIDIA runtime only.** Tdarr gets the GPU as a device reservation; never run it
  privileged to reach the GPU.
- **The Dozzle agent can read every container's logs and, through the Docker socket, control Docker**, for any
  Dozzle that connects to it with its certificate and key ([Dozzle's certificate](#dozzles-certificate)). Keep the
  key secret, and the agent's port (7007) on the LAN only.
- Keep `.env` at mode `600`; it holds no secrets by design, but it describes your host.
- Back up the services' data in `CONFIG_ROOT` ([upgrading.md](upgrading.md#backing-up)), nightly and off the host
  ([scheduled backups](#scheduled-backups)).
- Don't add services that mount the Docker socket, run privileged or use the host network without a documented
  reason; the policy check refuses them ([security.md](security.md)).
- Don't run an auto-updater (such as Watchtower) on these containers: it would replace the pinned, reviewed
  versions with whatever a tag points to today.

## Uninstalling

```sh
docker compose down          # stops and removes the containers and the stack's network
```

`CONFIG_ROOT` (the services' data, the logs and the transcode cache) and the media library are left untouched.
Remove the apps' folders under `CONFIG_ROOT` by hand if you no longer want the services' data.
