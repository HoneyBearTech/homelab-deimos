# Installing

The [quick start](quick-start.md) is the short version of this page.

## Requirements

- Linux with Docker Engine and the Compose v2 plugin (Docker Engine 25 or later and Compose 2.24 or later, for
  the health checks' `start_interval` and GPU device reservations). The reference host is **Ubuntu 24.04 on
  amd64**; every image is chosen to publish `linux/amd64`.
- For hardware transcoding: an **NVIDIA GPU** (passed through to the VM if the host is virtual), the NVIDIA
  driver, and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  configured for Docker (`sudo nvidia-ctk runtime configure --runtime=docker`, then restart Docker), and
  `COMPOSE_FILE=compose.yaml:compose.gpu.yaml` in `.env`. Without a GPU, leave that line out: Tdarr still runs
  and transcodes on the CPU.
- A user in the `docker` group to run `docker compose`. Membership is equivalent to root on the host, so keep
  that group small.
- The media library mounted on the host (local disk or a network share) and writable by `PUID`/`PGID`.
- Disk for Tdarr's transcode cache: room for several of your largest files at once, ideally on fast local disk
  rather than the network share.

## Where data lives

| What | Where on the host | In the container |
| --- | --- | --- |
| The media library | `MEDIA_PATH` | `/media` |
| Tdarr's database | `TDARR_SERVER_PATH` | `/app/server` |
| Tdarr's settings | `TDARR_CONFIGS_PATH` | `/app/configs` |
| Tdarr's logs | `TDARR_LOGS_PATH` | `/app/logs` |
| Tdarr's transcode cache | `TDARR_CACHE_PATH` | `/temp` |
| Spoolman's database | `SPOOLMAN_DATA_PATH` | `/home/app/.local/share/spoolman` |
| The AMS app's printer list (with the printers' access codes) | `AMS_PRINTERS_PATH` | `/app/printers` |
| The AMS app's logs | `AMS_LOGS_PATH` | `/app/logs` |

Every mount is listed in [interfaces.md](interfaces.md#volumes-and-mounts).

## Installing

1. Clone the repository (or download a release's source archive and verify it,
   [verifying-releases.md](verifying-releases.md)).
2. Create `.env` from `.env.example` (mode `600`) and set every value.
3. Create the data directories, then `docker compose up -d`.

**Adopting existing containers.** If the services already run on the host (from Portainer stacks or another
Compose project), point the stack at their existing data instead of starting empty: stop the old containers,
set each path in `.env` to where its data already is, and keep the same published ports and the **same
container path for the media library**: Tdarr stores every file's path in its database, so a different mount
point makes it see a new library. Take a backup of the old data first.

Running `main` instead of a release is possible but unsupported for anything you depend on.

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
2. **On this host**, as the user who runs the stack, install rsync and curl, and create a key used only for the
   backups, with an alias for it in `~/.ssh/config`:

   ```sh
   sudo apt install -y rsync curl
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
4. **Try it**: `scripts/scheduled-backup.sh`. Each service stops only while its own data is archived: Spoolman and
   the AMS app for seconds, Tdarr for as long as its database takes to compress, and a transcode it was running
   starts over.
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
- Back up the services' data directories ([upgrading.md](upgrading.md#backing-up)), nightly and off the host
  ([scheduled backups](#scheduled-backups)).
- Don't add services that mount the Docker socket, run privileged or use the host network without a documented
  reason; the policy check refuses them ([security.md](security.md)).
- Don't run an auto-updater (such as Watchtower) on these containers: it would replace the pinned, reviewed
  versions with whatever a tag points to today.

## Uninstalling

```sh
docker compose down          # stops and removes the containers and the stack's network
```

The data directories, the transcode cache and the media library are left untouched. Remove the data directories
by hand if you no longer want the services' data.
