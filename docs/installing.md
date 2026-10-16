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
| Tdarr's settings | `CONFIG_ROOT/tdarr/config` | `/app/configs` |
| Tdarr's database (libraries, flows, history) | `CONFIG_ROOT/tdarr/server` | `/app/server` |
| Tdarr's logs (not backed up) | `CONFIG_ROOT/tdarr/logs` | `/app/logs` |
| Tdarr's transcode cache (not backed up) | `CONFIG_ROOT/tdarr/transcode` | `/temp` |
| Spoolman's database | `CONFIG_ROOT/spoolman/config` | `/home/app/.local/share/spoolman` |
| The AMS app's printer list (with the printers' access codes) and settings | `CONFIG_ROOT/haspelsync/config` | `/app/printers` |
| The AMS app's logs (not backed up) | `CONFIG_ROOT/haspelsync/logs` | `/app/logs` |

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
     "$CONFIG_ROOT"/haspelsync/{config,logs}
   ```

**Adopting existing containers.** If the services already run on the host (from Portainer stacks or another
Compose project), move their existing data into the folders above instead of starting empty: stop the old
containers, copy each one's data into its folder with the owners kept (`cp -a`; they should be `PUID:PGID`), and
keep the same published ports and the **same container path for the media library**: Tdarr stores every file's
path in its database, so a different mount point makes it see a new library.
`docker inspect --format '{{json .Mounts}}' tdarr` shows what a container mounts where. Take a backup of the old data
first, and remove the old copies only once the stack runs from `CONFIG_ROOT`.

The maintainer's server runs `main` rather than a release, and deploys every merge by itself
([upgrading.md](upgrading.md#following-main)). For your own setup, a release is the safer choice.

## Scheduled backups

`scripts/scheduled-backup.sh` takes a backup (`scripts/backup.sh`), copies it to another machine with rsync, keeps
only the newest few in both places and reports to an Uptime Kuma push monitor. A systemd timer runs it nightly.
Every setting is in the optional `backup.env` ([interfaces.md](interfaces.md#backupenv)). The backups hold every
login and the printers' access codes: copy them only to a machine you trust as much as this one. The media library
is never in them; protect it with snapshots where it is stored.

1. **On the backup server**, create a folder for the backups and a user that can write only there and use rsync
   over SSH (on a Synology: a shared folder, a non-admin user with read/write on that folder only, rsync allowed
   under Application Privileges, SSH on, the rsync service on under File Services, and the user home service on so
   the user can have an `authorized_keys`). Whoever controls this host can delete or overwrite what it copied
   there, so keep older versions where the backup user can't reach them: snapshots of the folder, or a versioned
   copy of it made on the server after the nightly backup (on a Synology without Btrfs, a Hyper Backup task with
   rotation, into a folder the backup user has no access to). A recycle bin isn't enough: Synology's doesn't keep
   files deleted over rsync.
2. **On this host**, as the user who runs the stack, install rsync and curl (and pigz, which compresses the
   archives on every core instead of one), and create a key used only for the backups, with an alias for it in
   `~/.ssh/config`:

   ```sh
   sudo apt install -y rsync curl pigz
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
3. **Settings**: `cp backup.env.example backup.env && chmod 600 backup.env`, then set
   `BACKUP_REMOTE=backup-host:/volume1/<folder>` and, optionally, the retention and `BACKUP_PING_URL`. For the
   ping, add a monitor of type **Push** in Uptime Kuma with a heartbeat interval of 25 hours, and copy its URL.
4. **Try it**: `scripts/scheduled-backup.sh`. Each service stops only while its own data is copied (compression
   runs after it starts again): Spoolman and the AMS app for seconds, Tdarr for as long as its database takes to
   copy, and a transcode it was running starts over.
5. **Schedule it**, with the systemd user units in [`deploy/systemd/`](../deploy/systemd/) (they expect the
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
its `SHA256SUMS` first, and a copy that was cut off has none yet, so it is refused.

## Running it securely

- **Web UIs on the LAN only.** Tdarr, Spoolman and the AMS app mostly have no login of their own. Don't publish
  their ports beyond the LAN; Docker-published ports bypass host firewalls such as `ufw`, so restrict them at the
  router or with Docker's own `DOCKER-USER` rules, and use a reverse proxy's access lists for names you give them.
- **Turn on logins where a service offers them**, with long, unique passwords: HaspelSync's under Settings →
  Network access.
- **HaspelSync answers only the host names it knows.** Under its IP address it works as is; reached through a
  reverse proxy or under a DNS name, it refuses every request (`403`) until that name is added under Settings →
  Allowed host names. Its API answers only its own web UI and callers with an API key.
- **Protect the media library.** Tdarr replaces files in place. Keep snapshots on the storage that holds the
  library, check new flows on a small library first, and read Tdarr's release notes before upgrading it.
- **The GPU through the NVIDIA runtime only.** Tdarr gets the GPU as a device reservation; never run it
  privileged to reach the GPU.
- **The Dozzle agent can read every container's logs and, through the Docker socket, control Docker**, for any
  Dozzle that connects to it. Keep its port (7007) on the LAN only.
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
