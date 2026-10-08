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
| `PUID`, `PGID` | yes | `1000` | User and group Tdarr and Spoolman run as; Tdarr's must be able to read and write the media library. |
| `MEDIA_PATH` | yes | `/srv/media` | Host path of the media library. Never backed up by this stack. |
| `TDARR_SERVER_PATH` | yes | `/srv/appdata/tdarr/server` | Tdarr's database. |
| `TDARR_CONFIGS_PATH` | yes | `/srv/appdata/tdarr/configs` | Tdarr's settings. |
| `TDARR_LOGS_PATH` | yes | `/srv/appdata/tdarr/logs` | Tdarr's logs (not backed up). |
| `TDARR_CACHE_PATH` | yes | `/srv/transcode-cache` | Scratch space for transcodes in progress (not backed up). |
| `SPOOLMAN_DATA_PATH` | yes | `/srv/appdata/spoolman` | Spoolman's database. |
| `SPOOLMAN_PUBLIC_URL` | yes | `http://spoolman.example.com:7912` | The URL browsers reach Spoolman at; the AMS app's links point there. |
| `AMS_PRINTERS_PATH` | yes | `/srv/appdata/ams/printers` | The AMS app's printer list, including the printers' access codes in plain text. |
| `AMS_LOGS_PATH` | yes | `/srv/appdata/ams/logs` | The AMS app's logs (not backed up). |

No secret is a setting. If a service ever needs one in its environment, it gets its own gitignored
`<service>.env` (mode `600`) with a committed `<service>.env.example`, listed here.

### Fixed in `compose.yaml`

Tdarr runs its server with an internal node named `MyInternalNode` (its worker limits are stored under that name),
FFmpeg 7, its own login off (`auth: "false"`) and logs capped at 10 MB. The AMS app reaches Spoolman inside the
stack at `http://spoolman:8000`, in `automatic` mode, every two minutes, and never merges a spool that carries a
tag. Change these in `compose.yaml`, in a pull request.

## Services and ports

| Service | Image | Host port → container | What |
| --- | --- | --- | --- |
| Tdarr | `ghcr.io/haveagitgat/tdarr` | 8265 → 8265 | Web UI (LAN only) |
| | | 8266 → 8266 | Server port for Tdarr nodes on other machines |
| Spoolman | `ghcr.io/donkie/spoolman` | 7912 → 8000 | Web UI and REST API (LAN only) |
| AMS app (HaspelSync) | `ghcr.io/rdiger-36/bambulab-ams-spoolman-filamentstatus` | 4000 → 4000 | Web UI (LAN only) |

Exact versions and digests are in [`compose.yaml`](../compose.yaml). Each service has a health check: Tdarr's
`/api/v2/status`, Spoolman's `/api/v1/health`, and the AMS app's web UI.

## GPU

[`compose.gpu.yaml`](../compose.gpu.yaml) gives Tdarr the host's NVIDIA GPU as a Compose device reservation
(`driver: nvidia`, `count: all`, `capabilities: [gpu]`), which needs the NVIDIA driver and the NVIDIA Container
Toolkit on the host. It's a separate file so the stack also starts on a machine without a GPU, such as CI: set
`COMPOSE_FILE=compose.yaml:compose.gpu.yaml` in `.env` on the host. CI checks the policy with and without it.

## Volumes and mounts

| Container path | Host source | Service |
| --- | --- | --- |
| `/media` | `MEDIA_PATH` | Tdarr: the media library (read-write; never backed up) |
| `/app/server` | `TDARR_SERVER_PATH` | Tdarr: database (libraries, flows, history) |
| `/app/configs` | `TDARR_CONFIGS_PATH` | Tdarr: settings |
| `/app/logs` | `TDARR_LOGS_PATH` | Tdarr: logs (not backed up) |
| `/temp` | `TDARR_CACHE_PATH` | Tdarr: transcode cache (not backed up) |
| `/home/app/.local/share/spoolman` | `SPOOLMAN_DATA_PATH` | Spoolman: database |
| `/app/printers` | `AMS_PRINTERS_PATH` | AMS app: printer list with the printers' access codes |
| `/app/logs` | `AMS_LOGS_PATH` | AMS app: logs (not backed up) |

## Labels

| Label | Meaning |
| --- | --- |
| `org.honeybeartech.deimos.allow.<rule>` | Lets one service break one policy rule; the value is the reason, and must not be empty. Rules: `image`, `digest`, `latest`, `build`, `privileged`, `cap-add`, `host-network`, `host-pid`, `docker-socket`, `devices`, `healthcheck` ([security.md](security.md#policy)). None in use. |
| `org.honeybeartech.deimos.backup.skip` | Container paths (comma-separated, exact) that `scripts/backup.sh` never archives and `scripts/restore.sh` refuses to write, even if a backup lists them. Tdarr: `/media`, `/temp`, `/app/logs`; AMS app: `/app/logs`. |

## Commands

| Command | Does |
| --- | --- |
| `docker compose up -d` / `down` / `ps` / `logs <service>` | Runs and inspects the stack |
| `make check` | `docker compose config --format json \| python scripts/check_compose.py`: the policy check |
| `python scripts/check_compose.py [FILE] [--sbom OUT]` | Checks a resolved Compose config (from `FILE` or stdin); `--sbom` also writes a CycloneDX 1.6 SBOM of the images. Exit 0 = no violations, 1 = violations (one line each), 2 = unreadable input |
| `make test`, `make lint` | The checker's tests and the linters |
| `scripts/backup.sh [DIR]` | Stops the stack, archives every service's data mounts (every read-write volume or bind mount except the Docker socket, anonymous volumes and the paths in the service's `backup.skip` label: the media library, the transcode cache and the logs), `.env` and any `<service>.env` into `DIR` (default `backups/<date>-<time>`, gitignored) with a `MANIFEST` and `SHA256SUMS`, all mode `600`, then starts what was running |
| `scripts/restore.sh [--yes] DIR [SERVICE...]` | Verifies `DIR/SHA256SUMS`, checks the `MANIFEST`, asks for confirmation (unless `--yes`), stops the services, replaces the contents of each listed mount with its archive, and starts what was running. Writes only mounts the service still has read-write; never the Docker socket or a path in the service's `backup.skip` label |
| `make smoke` | `scripts/smoke-test.sh`: starts every service under a separate Compose project with throwaway directories, no GPU and no published ports, waits until all are healthy, round-trips a backup and restore over every data mount, checks that excluded mounts were neither archived nor overwritten, then removes what it created. Exit 0 = all healthy and restored |

## Outbound connections

From the host: the image registries (GitHub Container Registry) on `docker compose pull`; the media library's
storage. From the services: Tdarr downloads its components and plugin updates from its project's servers;
Tdarr nodes on other machines connect to port 8266; the AMS app connects to each printer on the LAN over MQTT
(port 8883) and FTPS (port 990), and to Spoolman inside the stack.

## Release files

Each GitHub Release has `homelab-deimos-<version>.tar.gz` (source, with `LICENSE`),
`homelab-deimos-<version>.cdx.json` (CycloneDX SBOM of the pinned images), `SHA256SUMS` and its Sigstore
bundle, and SLSA provenance ([verifying-releases.md](verifying-releases.md)).
