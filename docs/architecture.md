# Architecture

homelab-deimos is the Docker Compose definition of **Deimos**, a homelab server for media processing and 3D
printing: Tdarr, which keeps the media library in consistent formats using the GPU, and the services that track
the 3D printers' filament. Deimos is an Ubuntu 24.04 virtual machine on amd64 with an NVIDIA GPU passed through.
The repository holds configuration, not application code: the services run from their upstream images, pinned by
digest.

## Services

| Service | Image source | Role |
| --- | --- | --- |
| Tdarr (server with an internal node) | the project's own image | Scans the media library and runs transcode and health-check flows on it, on the GPU (NVENC/NVDEC) or the CPU; other machines can join as extra nodes |
| Spoolman | the project's own image | Inventory of filament spools: what's loaded, how much is left; web UI and REST API |
| autoheal | the project's own image | Restarts any service whose health check fails, through socket-proxy; can post a notice to a webhook |
| socket-proxy | LinuxServer.io's image | A filter in front of the Docker socket that lets autoheal only list, inspect, restart and stop containers |
| AMS app ([HaspelSync](https://github.com/Rdiger-36/HaspelSync)) | the project's own image | Listens to the Bambu Lab printers over MQTT, recognises the spools in their automatic material systems, links them to Spoolman's spools and books what each print used |

Other services on the same host (a local LLM chat and model server, monitoring agents, a Docker management
agent) come from their own projects; this stack doesn't include or manage them.

## Actors and actions

| Actor | Does |
| --- | --- |
| Maintainer | Merges pull requests, tags releases, runs `git pull` / `docker compose up -d` on the host, configures each service in its web UI |
| Dependabot | Opens a pull request when an image (tag and digest), a check tool or an Action has a new version; patch and minor updates are auto-merged once the checks pass |
| CI | Lints, scans for secrets, tests the checker, resolves the Compose file, enforces the policy and smoke-tests the stack on amd64 (without a GPU) on every pull request |
| Release workflow | On a version tag: checks the policy, writes the SBOM, signs the checksums, publishes the GitHub Release |
| LAN users | Use the web UIs, usually through the homelab's reverse proxy |
| Tdarr nodes on other machines | Connect to Tdarr's server port to take transcode jobs (optional) |
| The printers | Report their material systems and prints to the AMS app over MQTT; serve the sliced files over FTPS |
| Home automation | Reads Spoolman through its API (optional) |

## Data flow

```
LAN clients ──HTTP(S) via reverse proxy──▶ Tdarr UI · Spoolman UI/API · AMS app

Tdarr ──read/write──▶ media library (network share or local disk)
Tdarr ──NVIDIA runtime──▶ GPU
Tdarr ──scratch files──▶ transcode cache
Tdarr nodes elsewhere ──server port──▶ Tdarr

AMS app ──MQTT / FTPS──▶ printers on the LAN
AMS app ──REST──▶ Spoolman (inside the stack)

autoheal ──(internal network)──▶ socket-proxy ──read-only socket──▶ Docker: restarts unhealthy containers
```

Each service keeps its settings and database in its own data directory (see
[interfaces.md](interfaces.md#volumes-and-mounts)), which is what `scripts/backup.sh` archives. The media library
and the transcode cache are never archived.

## How changes reach the host

1. Dependabot (or the maintainer) opens a pull request that changes an image's tag and digest.
2. CI resolves the Compose file, runs the policy check and the smoke test; the maintainer reads the service's
   release notes.
3. The pull request is squash-merged (automatically for Dependabot's patch and minor updates, once every
   check passes); a version tag makes a signed release.
4. On the host: back up, `git checkout <tag>`, `docker compose pull && docker compose up -d`
   ([upgrading.md](upgrading.md)).

Nothing on the host updates itself: a version that runs is always a version that's in git.

## Repository layout

| Path | What |
| --- | --- |
| `compose.yaml` | The stack |
| `compose.gpu.yaml` | Tdarr's GPU reservation, for hosts with an NVIDIA GPU |
| `.env.example` | Template for the settings |
| `scripts/check_compose.py` | The policy check and SBOM generator (standard-library Python) |
| `scripts/backup.sh`, `scripts/restore.sh` | Backup and restore of every service's data, never the media library |
| `scripts/smoke-test.sh` | Starts the stack in isolation, waits for health, and round-trips a backup |
| `tests/` | The checker's tests, with JSON fixtures |
| `docs/` | This documentation |
| `.github/` | CI, release and security workflows, Dependabot, templates |
