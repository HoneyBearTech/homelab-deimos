# Interfaces

Everything homelab-deimos reads, exposes or runs. homelab-deimos has no HTTP API of its own; the services' web
UIs and APIs are documented by their projects.

> **Planned:** `compose.yaml` isn't in the repository yet. The settings, services, ports and mounts below are the
> expected ones (the upstream defaults); they are confirmed or corrected when the stack is added, and the AMS
> app's are added then. The scripts and commands already exist.

## Settings

### `.env`

Read by `docker compose` from `.env` next to `compose.yaml` (template: [`.env.example`](../.env.example)). A
setting marked required stops `docker compose` with an error naming it when it's missing.

| Setting | Required | Example | Meaning |
| --- | --- | --- | --- |
| `TZ` | yes | `Etc/UTC` | Time zone (tz database name) for logs and schedules. |
| `PUID`, `PGID` | yes | `1000` | User and group Tdarr runs as; must be able to read and write the media library. |
| `MEDIA_PATH` | yes | `/srv/media` | Host path of the media library. Never backed up by this stack. |
| `TDARR_SERVER_PATH` | yes | `/srv/appdata/tdarr/server` | Tdarr's database. |
| `TDARR_CONFIGS_PATH` | yes | `/srv/appdata/tdarr/configs` | Tdarr's settings. |
| `TDARR_LOGS_PATH` | yes | `/srv/appdata/tdarr/logs` | Tdarr's logs. |
| `TDARR_CACHE_PATH` | yes | `/srv/transcode-cache` | Scratch space for transcodes in progress. |
| `SPOOLMAN_DATA_PATH` | yes | `/srv/appdata/spoolman` | Spoolman's database. |

No secret is a setting. If a service ever needs one in its environment, it gets its own gitignored
`<service>.env` (mode `600`) with a committed `<service>.env.example`, listed here.

## Services and ports

| Service | Image | Host port → container | What |
| --- | --- | --- | --- |
| Tdarr | `ghcr.io/haveagitgat/tdarr` | 8265 → 8265 | Web UI (LAN only) |
| | | 8266 → 8266 | Server port for extra Tdarr nodes (only if other machines run nodes) |
| Spoolman | `ghcr.io/donkie/spoolman` | 7912 → 8000 | Web UI and REST API (LAN only) |
| AMS app | to be identified | 4000 → to be settled | Web UI (LAN only) |

Exact versions and digests will be in [`compose.yaml`](../compose.yaml).

## GPU

Tdarr gets the host's NVIDIA GPU as a Compose device reservation (`driver: nvidia`, `capabilities: [gpu]`),
which needs the NVIDIA Container Toolkit on the host. **Planned:** the reservation lives in a separate override
file, so the stack also starts on a machine without a GPU (such as CI).

## Volumes and mounts

| Container path | Host source | Service |
| --- | --- | --- |
| `/media` | `MEDIA_PATH` | Tdarr: the media library (read-write; never backed up) |
| `/app/server` | `TDARR_SERVER_PATH` | Tdarr: database (libraries, flows, history) |
| `/app/configs` | `TDARR_CONFIGS_PATH` | Tdarr: settings |
| `/app/logs` | `TDARR_LOGS_PATH` | Tdarr: logs (not backed up) |
| `/temp` | `TDARR_CACHE_PATH` | Tdarr: transcode cache (not backed up) |
| `/home/app/.local/share/spoolman` | `SPOOLMAN_DATA_PATH` | Spoolman: database |

## Labels

| Label | Meaning |
| --- | --- |
| `org.honeybeartech.deimos.allow.<rule>` | Lets one service break one policy rule; the value is the reason, and must not be empty. Rules: `image`, `digest`, `latest`, `build`, `privileged`, `cap-add`, `host-network`, `host-pid`, `docker-socket`, `healthcheck` ([security.md](security.md#policy)). None agreed yet. |
| `org.honeybeartech.deimos.backup.skip` | Container paths (comma-separated, exact) that `scripts/backup.sh` never archives and `scripts/restore.sh` refuses to write, even if a backup lists them. Planned for Tdarr: `/media`, `/temp`, `/app/logs`. |

## Commands

| Command | Does |
| --- | --- |
| `docker compose up -d` / `down` / `ps` / `logs <service>` | Runs and inspects the stack |
| `make check` | `docker compose config --format json \| python scripts/check_compose.py`: the policy check |
| `python scripts/check_compose.py [FILE] [--sbom OUT]` | Checks a resolved Compose config (from `FILE` or stdin); `--sbom` also writes a CycloneDX 1.6 SBOM of the images. Exit 0 = no violations, 1 = violations (one line each), 2 = unreadable input |
| `make test`, `make lint` | The checker's tests and the linters |
| `scripts/backup.sh [DIR]` | Stops the stack, archives every service's data mounts (every read-write volume or bind mount except the Docker socket, anonymous volumes and the paths in the service's `backup.skip` label: the media library, the transcode cache and the logs), `.env` and any `<service>.env` into `DIR` (default `backups/<date>-<time>`, gitignored) with a `MANIFEST` and `SHA256SUMS`, all mode `600`, then starts what was running |
| `scripts/restore.sh [--yes] DIR [SERVICE...]` | Verifies `DIR/SHA256SUMS`, checks the `MANIFEST`, asks for confirmation (unless `--yes`), stops the services, replaces the contents of each listed mount with its archive, and starts what was running. Writes only mounts the service still has read-write; never the Docker socket or a path in the service's `backup.skip` label |
| `make smoke` | `scripts/smoke-test.sh`: starts every service under a separate Compose project with throwaway directories, no GPU and no published ports, waits until all are healthy, round-trips a backup and restore over every data mount, checks that excluded mounts were neither archived nor overwritten, then removes what it created. Exit 0 = all healthy and restored (or no `compose.yaml` yet) |

## Outbound connections

From the host: the image registries (GitHub Container Registry, Docker Hub) on `docker compose pull`; the media
library's storage. From the services: Tdarr downloads its components and plugin updates from its project's
servers on start; the AMS app may talk to the printers or the printer maker's cloud (to be confirmed).

## Release files

Each GitHub Release will have `homelab-deimos-<version>.tar.gz` (source, with `LICENSE`),
`homelab-deimos-<version>.cdx.json` (CycloneDX SBOM of the pinned images), `SHA256SUMS` and its Sigstore
bundle, and SLSA provenance ([verifying-releases.md](verifying-releases.md)).
