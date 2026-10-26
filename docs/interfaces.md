# Interfaces

Everything homelab-deimos reads, exposes or runs. homelab-deimos has no HTTP API of its own; the services' web
UIs and APIs are documented by their projects.

## Settings

### `.env`

Read by `docker compose` from `.env` next to `compose.yaml` (template: [`.env.example`](../.env.example)). A
setting marked required stops `docker compose` with an error naming it when it's missing.

| Setting | Required | Example | Meaning |
| --- | --- | --- | --- |
| `COMPOSE_FILE` | no | `compose.yaml:compose.gpu.yaml` | Set on a host with an NVIDIA GPU, so Tdarr gets it ([GPU](#gpu)). Leave unset without one. |
| `TZ` | yes | `Etc/UTC` | Time zone (tz database name) for logs and schedules. |
| `PUID`, `PGID` | yes | `1000` | User and group Tdarr, Spoolman and the AMS app run as; Tdarr's must be able to read and write the media library. |
| `MEDIA_PATH` | yes | `/srv/media` | Host path of the media library. Never backed up by this stack. |
| `CONFIG_ROOT` | yes | `/srv/config` | Host folder holding every app's data, one folder per app: `CONFIG_ROOT/<app>/config` and any other folder beside it ([where data lives](installing.md#where-data-lives)). Never inside `/var/lib/docker/volumes`. |
| `SPOOLMAN_PUBLIC_URL` | yes | `http://spoolman.example.com:7912` | The URL browsers reach Spoolman at; the AMS app's links point there. |

No secret is a setting. A secret a service can only take from its environment gets its own gitignored
`<service>.env` (mode `600`) with a committed `<service>.env.example`:

| File | Setting | Meaning |
| --- | --- | --- |
| `autoheal.env` (optional; template [`autoheal.env.example`](../autoheal.env.example)) | `WEBHOOK_URL` | Where autoheal posts a notice for each restart (a Discord channel webhook works as is). A secret: anyone with it can post to the channel. |

### `backup.env`

Read by `scripts/scheduled-backup.sh` (template: [`backup.env.example`](../backup.env.example)); optional, mode
`600`, gitignored. Read as data, never run: only these keys, one `KEY=value` per line; anything else is an error.

| Setting | Default | Meaning |
| --- | --- | --- |
| `BACKUP_DIR` | `backups` | Where backups are written (relative to the checkout, or absolute). |
| `BACKUP_KEEP` | `3` | How many backups are kept in `BACKUP_DIR`; older ones are deleted. |
| `BACKUP_REMOTE` | empty | rsync destination each backup is copied to, such as `backup-host:/volume1/deimos-backups` (an SSH alias); empty = no copy. |
| `BACKUP_REMOTE_KEEP` | `30` | How many backups are kept at `BACKUP_REMOTE`; older ones are deleted. |
| `BACKUP_PING_URL` | empty | An Uptime Kuma push URL (a secret), told `up` after each good run and `down` after a failed one. |
| `BACKUP_AGE_RECIPIENT` | empty | An age public key (`age1...`). When set, the copy at `BACKUP_REMOTE` is only `backup.tar.age` (the backup directory as one tar archive, encrypted to that key) and its `SHA256SUMS`; `age -d -i <private key> backup.tar.age \| tar -x` gives back the backup directory that `scripts/restore.sh` reads. Needs `age` on the host. Empty = the backup is copied as it is. |

Only directories named like a backup (`<date>-<time>`) are ever deleted, here or at the remote.

### Fixed in `compose.yaml`

Tdarr runs its server with an internal node named `MyInternalNode` (its worker limits are stored under that name),
FFmpeg 7, its own login off (`auth: "false"`) and logs capped at 10 MB. The AMS app reaches Spoolman inside the
stack at `http://spoolman:8000`, in `automatic` mode, every two minutes, and never merges a spool that carries a
tag. Since HaspelSync 1.3 these only seed its settings: once a setting is saved in its web UI, `settings.json` in
`/app/printers` owns it and the variable is ignored. Change them in `compose.yaml`, in a pull request, or in the web
UI, whose settings the backups cover.

## Services and ports

| Service | Image | Host port → container | What |
| --- | --- | --- | --- |
| Tdarr | `ghcr.io/haveagitgat/tdarr` | 8265 → 8265 | Web UI (LAN only) |
| | | 8266 → 8266 | Server port for Tdarr nodes on other machines |
| Spoolman | `ghcr.io/donkie/spoolman` | 7912 → 8000 | Web UI and REST API (LAN only) |
| AMS app (HaspelSync) | `ghcr.io/rdiger-36/haspelsync` | 4000 → 4000 | Web UI (LAN only; set a password under Settings → Network access) |
| autoheal | `willfarrell/autoheal` | none | Restarts containers labelled `autoheal: "true"` whose health check fails |
| socket-proxy | `lscr.io/linuxserver/socket-proxy` | none (internal network) | The only way autoheal reaches Docker: list, inspect, restart, stop |
| Dozzle agent | `amir20/dozzle` | 7007 → 7007 | Agent for the homelab's Dozzle on another host (LAN only); no web UI of its own |

Exact versions and digests are in [`compose.yaml`](../compose.yaml). Each service has a health check: Tdarr's
`/api/v2/status`, Spoolman's `/api/v1/health`, the AMS app's web UI, autoheal's process, the proxy's `/_ping` and
the Dozzle agent's own `/dozzle healthcheck`. autoheal checks every 10 seconds and gives a service 30 seconds to
stop before restarting it; a service gets 10 minutes after it starts before failed checks count.

## GPU

[`compose.gpu.yaml`](../compose.gpu.yaml) gives Tdarr the host's NVIDIA GPU as a Compose device reservation
(`driver: nvidia`, `count: all`, `capabilities: [gpu]`), which needs the NVIDIA driver, the NVIDIA Container
Toolkit (1.18 or later) and Docker Engine 29.2 or later on the host: from 29.2 Docker serves it through CDI, so Tdarr
keeps the GPU through `systemctl daemon-reload` ([installing.md](installing.md#the-gpu-and-docker-292)). It's a
separate file so the stack also starts on a machine without a GPU, such as CI: set
`COMPOSE_FILE=compose.yaml:compose.gpu.yaml` in `.env` on the host. CI checks the policy with and without it.

## Volumes and mounts

| Container path | Host source | Service |
| --- | --- | --- |
| `/media` | `MEDIA_PATH` | Tdarr: the media library (read-write; never backed up) |
| `/app/configs` | `CONFIG_ROOT/tdarr/config` | Tdarr: settings |
| `/app/server` | `CONFIG_ROOT/tdarr/server` | Tdarr: database (libraries, flows, history) |
| `/app/logs` | `CONFIG_ROOT/tdarr/logs` | Tdarr: logs (not backed up) |
| `/temp` | `CONFIG_ROOT/tdarr/transcode` | Tdarr: transcode cache (not backed up) |
| `/home/app/.local/share/spoolman` | `CONFIG_ROOT/spoolman/config` | Spoolman: database |
| `/app/printers` | `CONFIG_ROOT/haspelsync/config` | AMS app: printer list with the printers' access codes, and the settings saved in its web UI |
| `/app/logs` | `CONFIG_ROOT/haspelsync/logs` | AMS app: logs (not backed up) |
| `/var/run/docker.sock` (read-only) | the Docker socket | socket-proxy and the Dozzle agent (allowed exceptions, see below) |
| `/run/secrets/dozzle_cert`, `/run/secrets/dozzle_key` (read-only; Docker secrets) | `CONFIG_ROOT/dozzle/certs/dozzle_cert.pem` and `dozzle_key.pem`, owned by root ([installing.md](installing.md#dozzles-certificate)) | the Dozzle agent (`DOZZLE_CERT`, `DOZZLE_KEY` point at them) |

## Labels

| Label | Meaning |
| --- | --- |
| `org.honeybeartech.deimos.allow.<rule>` | Lets one service break one policy rule; the value is the reason, and must not be empty. Rules: `image`, `digest`, `latest`, `build`, `privileged`, `no-new-privileges`, `cap-drop`, `cap-add`, `host-network`, `host-pid`, `docker-socket`, `devices`, `healthcheck` ([security.md](security.md#policy)). In use: socket-proxy (`docker-socket`), autoheal (`latest`), the Dozzle agent (`docker-socket`). |
| `autoheal` | `"true"` on every service autoheal may restart: Tdarr, Spoolman, the AMS app, socket-proxy and the Dozzle agent. |
| `org.honeybeartech.deimos.backup.skip` | Container paths (comma-separated, exact) that `scripts/backup.sh` never archives and `scripts/restore.sh` refuses to write, even if a backup lists them. Tdarr: `/media`, `/temp`, `/app/logs`; AMS app: `/app/logs`. |
| `org.honeybeartech.deimos.backup.exclude` | Paths inside a service's data mounts (comma-separated, exact container paths, at any depth) that `scripts/backup.sh` leaves out of the mount's archive and `scripts/restore.sh` leaves in place. A path that isn't inside one of the service's data mounts, or contains `..` or a pattern character (`*?[\`), makes both fail before anything stops. Not in use. |

## Commands

| Command | Does |
| --- | --- |
| `docker compose up -d` / `down` / `ps` / `logs <service>` | Runs and inspects the stack |
| `make check` | `docker compose config --format json \| python scripts/check_compose.py`: the policy check |
| `python scripts/check_compose.py [FILE] [--sbom OUT]` | Checks a resolved Compose config (from `FILE` or stdin); `--sbom` also writes a CycloneDX 1.6 SBOM of the images. Exit 0 = no violations, 1 = violations (one line each), 2 = unreadable input |
| `make test`, `make lint` | The checker's tests and the linters |
| `scripts/backup.sh [DIR]` | Archives every service's data mounts (every read-write volume or bind mount except the Docker socket, anonymous volumes and the paths in the service's `backup.skip` label: the media library, the transcode cache and the logs; a mount inside another mount is archived on its own and left out of the outer archive), `.env` and any `<service>.env` into `DIR` (default `backups/<date>-<time>`, gitignored) with a `MANIFEST`, `NETWORKS` (the address ranges of networks the stack joins but doesn't create), `VERSION` (the checkout's `git describe`) and `SHA256SUMS`, all mode `600`. Each service is stopped only while its own mounts are copied and started again (with any service Compose stopped along with it) before the copies are compressed (with `pigz` when it's installed, else `gzip`). Leaves out the paths a service's `org.honeybeartech.deimos.backup.exclude` label lists. Before anything stops it waits for a deploy by the pull agent to finish and holds the agent's lock until it exits ([upgrading.md](upgrading.md#backing-up); up to 30 minutes, then it fails before anything stops); only where the agent keeps state for the stack, in `$HOMELAB_PULL_STATE_DIR/deimos` (default `${XDG_STATE_HOME:-~/.local/state}/homelab-pull/deimos`). If stopping a service fails while its container is still on its way down (a process stuck writing to a network share can outlast Docker's stop), it waits up to `BACKUP_STOP_WAIT` seconds (default 120) for the container to exit and carries on; if it still runs then, the backup fails, and the services are started again only after a second wait. Fails, naming them, if any service that was running when it began isn't running again at the end. Exit 0 = backup complete |
| `scripts/scheduled-backup.sh` | Runs `scripts/backup.sh` into `BACKUP_DIR/<date>-<time>`, copies it to `BACKUP_REMOTE` with rsync (`SHA256SUMS` last; with `BACKUP_AGE_RECIPIENT`, only the encrypted `backup.tar.age` and its `SHA256SUMS`), deletes all but the newest `BACKUP_REMOTE_KEEP` there and `BACKUP_KEEP` here, and reports to `BACKUP_PING_URL` ([`backup.env`](#backupenv)). Run nightly by the systemd user units in `deploy/systemd/`. Exit 0 = backup complete and copied |
| `scripts/restore.sh [--yes] DIR [SERVICE...]` | Verifies `DIR/SHA256SUMS`, checks the `MANIFEST`, creates missing containers and volumes, asks for confirmation (unless `--yes`), stops the services, replaces the contents of each listed mount with its archive (owners and modes kept), and starts what was running. Writes only mounts the service still has read-write; never the Docker socket, a path in the service's `backup.skip` label, another mount inside the one it restores, or a path in its `backup.exclude` label. A service that bind-mounts a folder inside a mount it restores is stopped and started again with it, so it doesn't keep the replaced folder. Waits for and holds the pull agent's lock like `scripts/backup.sh`. An encrypted copy is decrypted first ([rebuilding.md](rebuilding.md#an-encrypted-backup)) |
| `make smoke` | `scripts/smoke-test.sh`: starts every service under a separate Compose project with throwaway directories, no GPU and no published ports, waits until all are healthy, round-trips a backup and restore over every data mount (checking `NETWORKS` and `VERSION`, that skipped mounts were neither archived nor overwritten and that excluded paths were neither archived nor deleted; a test container that mounts a folder inside another app's data is started again with it), runs the scheduled backup against a stand-in backup server (and, when `age` is installed, the encrypted copy: only the archive and its checksums leave, the right key opens it, another key doesn't), makes a test container unhealthy and checks that autoheal restarts it, then removes what it created. A stack's own steps, when it has any, are in `scripts/smoke-stack.sh` (`smoke_prepare`, `smoke_check`); a `*_SUBNET` setting gets a free address range. Exit 0 = all passed |

## Outbound connections

From the host: the image registries (GitHub Container Registry) on `docker compose pull`; the media library's
storage. From the services: Tdarr downloads its components and plugin updates from its project's servers;
Spoolman fetches the SpoolmanDB filament catalogue from `donkie.github.io`; Tdarr nodes on
other machines connect to port 8266; the AMS app connects to each printer on the LAN over MQTT
(port 8883) and FTPS (port 990), and to Spoolman inside the stack. The scheduled backup to `BACKUP_REMOTE` (rsync,
usually over SSH) and to `BACKUP_PING_URL`, if they are set.

## Release files

Each GitHub Release has `homelab-deimos-<version>.tar.gz` (source, with `LICENSE`),
`homelab-deimos-<version>.cdx.json` (CycloneDX SBOM of the pinned images), `SHA256SUMS` and its Sigstore
bundle, and SLSA provenance ([verifying-releases.md](verifying-releases.md)).
