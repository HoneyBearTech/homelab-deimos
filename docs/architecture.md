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
| Dozzle agent | the project's own image | Agent that serves the containers' logs (read through the Docker socket) to the homelab's Dozzle on another host, which shows every host's logs in one web UI |
| AMS app ([HaspelSync](https://github.com/Rdiger-36/HaspelSync)) | the project's own image | Listens to the Bambu Lab printers over MQTT, recognises the spools in their automatic material systems, links them to Spoolman's spools and books what each print used, read from the print's sliced file (fetched over FTPS) |

Other services on the same host (a local LLM chat and model server, monitoring agents, a Docker management
agent) come from their own projects; this stack doesn't include or manage them.

## Actors and actions

| Actor | Does |
| --- | --- |
| Maintainer | Merges pull requests (a merge deploys), tags releases, configures each service in its web UI |
| Dependabot | Opens a pull request when an image (tag and digest), a check tool or an Action has a new version; patch, minor and major updates and image rebuilds are auto-merged once the checks pass, except Tdarr's |
| CI | Lints, scans for secrets, tests the checker, resolves the Compose file, enforces the policy and smoke-tests the stack on amd64 (without a GPU) on every pull request |
| Pull agent | On the maintainer's server, about every 90 seconds: fetches `main` and, when it moved, fast-forwards the checkout and runs `docker compose pull` and `docker compose up -d` (it lives in the maintainer's private orchestration repository); skips its run while a backup or restore holds its lock |
| Release workflow | On a version tag: checks the policy, writes the SBOM, signs the checksums, publishes the GitHub Release |
| LAN users | Use the web UIs, usually through the homelab's reverse proxy |
| Tdarr nodes on other machines | Connect to Tdarr's server port to take transcode jobs (optional) |
| The printers | Report their material systems and prints to the AMS app over MQTT; serve the sliced files over FTPS |
| Home automation | Reads Spoolman through its API (optional) |
| The homelab's Dozzle | Connects to the Dozzle agent's port to show this host's container logs in its web UI |

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
Dozzle on another host ──port 7007──▶ Dozzle agent ──read-only socket──▶ Docker: container logs
```

Each service keeps its settings and database in its own data directory (see
[interfaces.md](interfaces.md#volumes-and-mounts)), which is what `scripts/backup.sh` archives. The media library
and the transcode cache are never archived.

## How changes reach the host

1. Dependabot (or the maintainer) opens a pull request that changes an image's tag and digest.
2. CI resolves the Compose file, runs the policy check and the smoke test; the maintainer reads the service's
   release notes.
3. The pull request is squash-merged (automatically for Dependabot's patch, minor and major updates and image
   rebuilds, once every check passes, except Tdarr's; by the maintainer, after a backup, for anything else that can
   migrate data).
4. Within about 90 seconds the pull agent on the host sees `main` move, fast-forwards its checkout and runs
   `docker compose pull && docker compose up -d --remove-orphans --wait`
   ([upgrading.md](upgrading.md#following-main)). A rollback is a revert pull request, deployed the same way.
   While `scripts/backup.sh` or `scripts/restore.sh` runs, the agent waits: they share a lock, so a deploy never
   overlaps a backup or a restore ([upgrading.md](upgrading.md#backing-up)).
5. A version tag makes a signed release: a named, verifiable snapshot of `main`.

Nothing updates from a registry by itself: a version that runs is always a version that's in `main`. The host
only pulls; nothing connects in to it to deploy.

## Repository layout

| Path | What |
| --- | --- |
| `compose.yaml` | The stack |
| `compose.gpu.yaml` | Tdarr's GPU reservation, for hosts with an NVIDIA GPU |
| `.env.example` | Template for the settings |
| `scripts/check_compose.py` | The policy check and SBOM generator (standard-library Python) |
| `scripts/backup.sh`, `scripts/restore.sh` | Backup and restore of every service's data, never the media library |
| `scripts/scheduled-backup.sh`, `deploy/systemd/` | Nightly backup, copied off the host with rsync and pruned, reported to an Uptime Kuma push monitor |
| `scripts/smoke-test.sh` | Starts the stack in isolation, waits for health, and round-trips a backup |
| `tests/` | The checker's tests, with JSON fixtures |
| `docs/` | This documentation |
| `.github/` | CI, release and security workflows, Dependabot, templates |
