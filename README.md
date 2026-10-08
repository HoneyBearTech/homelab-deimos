# homelab-deimos

[![CI](https://github.com/HoneyBearTech/homelab-deimos/actions/workflows/ci.yml/badge.svg)](https://github.com/HoneyBearTech/homelab-deimos/actions/workflows/ci.yml)
[![CodeQL](https://github.com/HoneyBearTech/homelab-deimos/actions/workflows/codeql.yml/badge.svg)](https://github.com/HoneyBearTech/homelab-deimos/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/HoneyBearTech/homelab-deimos/badge)](https://scorecard.dev/viewer/?uri=github.com/HoneyBearTech/homelab-deimos)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15290/badge)](https://www.bestpractices.dev/projects/15290)
[![OpenSSF Baseline](https://www.bestpractices.dev/projects/15290/baseline)](https://www.bestpractices.dev/projects/15290)

Docker Compose stack for Deimos, a homelab Ubuntu server (24.04, amd64, with an NVIDIA GPU). Version-pinned, self-hosted services, kept as code for easy upgrades and rebuilds.

> [!WARNING]
> Tdarr rewrites the files in your media library: a broken upgrade or a misconfigured flow can damage or delete
> them, and this stack does not back the library up. Keep snapshots of the library on the storage that holds it,
> and back up the services' data before every upgrade ([docs/upgrading.md](docs/upgrading.md)).

> [!NOTE]
> **Planned:** the stack itself (`compose.yaml`) isn't in the repository yet. The documentation describes what
> it will be; everything not built yet is marked **Planned**.

## Documentation

- [Quick start](docs/quick-start.md): getting the stack running on a fresh Docker host
- [Installing](docs/installing.md): host preparation (including the NVIDIA runtime), where data lives, running it securely, uninstalling
- [Upgrading](docs/upgrading.md): moving to a new release, backup and restore, rolling back
- [Rebuilding](docs/rebuilding.md): a new or wiped host, from a backup
- [Architecture](docs/architecture.md): the services, actors, data flow and how updates reach the host
- [Interfaces](docs/interfaces.md): every setting, port, volume, label and command
- [Verifying releases](docs/verifying-releases.md): checking signatures, checksums, provenance and the SBOM
- [Security requirements](docs/security.md): what the stack protects, what it doesn't, where secrets live
- [Assurance case](docs/assurance-case.md): threat model, trust boundaries, secure design, common weaknesses
- [Dependencies](docs/dependencies.md): how images and tools are chosen, pinned, tracked and patched
- [Roadmap](docs/roadmap.md): the next year, and what homelab-deimos will not do
- Project policies: [CONTRIBUTING](CONTRIBUTING.md) · [SECURITY](SECURITY.md) · [GOVERNANCE](GOVERNANCE.md) ·
  [SUPPORT](SUPPORT.md) · [CODE OF CONDUCT](CODE_OF_CONDUCT.md) · [CHANGELOG](CHANGELOG.md)

## What's in the stack

**Planned** ([architecture](docs/architecture.md), ports in [interfaces](docs/interfaces.md#services-and-ports)):

- **Tdarr**: transcodes and health-checks the media library, using the GPU (NVENC/NVDEC)
- **Spoolman**: an inventory of 3D-printer filament spools, with a web UI and an API
- **The AMS app**: a companion service for the printers' automatic material systems (to be confirmed when the
  stack is added)

Every service will have a health check, and every image will be pinned by tag **and** digest, for
`linux/amd64`. New versions arrive as Dependabot pull requests that CI checks and the maintainer merges;
nothing on the host updates itself.

## Getting started

**Planned**, once `compose.yaml` exists:

```sh
git clone https://github.com/HoneyBearTech/homelab-deimos.git && cd homelab-deimos
cp .env.example .env && chmod 600 .env              # then set TZ, PUID/PGID and the paths
. ./.env && mkdir -p "$TDARR_SERVER_PATH" "$TDARR_CONFIGS_PATH" "$TDARR_LOGS_PATH" "$TDARR_CACHE_PATH" "$SPOOLMAN_DATA_PATH"
docker compose up -d --wait
```

The host needs the NVIDIA driver and the NVIDIA Container Toolkit for Tdarr to use the GPU
([installing](docs/installing.md#requirements)). The full steps are in the [quick start](docs/quick-start.md).

## Usage

```sh
docker compose ps                 # what's running
docker compose logs -f <service>  # one service's log
make check                        # policy check: every image pinned, nothing privileged
scripts/backup.sh                 # back up every service's data, never the media library
```

Upgrading to a new release: [docs/upgrading.md](docs/upgrading.md).

## Configuration

Settings come from `.env` (template [`.env.example`](.env.example)), which holds no secrets. **Planned**: the
list is settled when the stack is added.

| Setting | Default in `.env.example` | Meaning |
| --- | --- | --- |
| `TZ` | `Etc/UTC` | Time zone |
| `PUID`, `PGID` | `1000` | User and group Tdarr runs as; must be able to read and write the media library |
| `MEDIA_PATH` | `/srv/media` | The media library Tdarr processes (never backed up by this stack) |
| `TDARR_SERVER_PATH` | `/srv/appdata/tdarr/server` | Tdarr's database |
| `TDARR_CONFIGS_PATH` | `/srv/appdata/tdarr/configs` | Tdarr's settings |
| `TDARR_LOGS_PATH` | `/srv/appdata/tdarr/logs` | Tdarr's logs |
| `TDARR_CACHE_PATH` | `/srv/transcode-cache` | Scratch space for transcodes in progress |
| `SPOOLMAN_DATA_PATH` | `/srv/appdata/spoolman` | Spoolman's database |

Ports, volumes and labels: [docs/interfaces.md](docs/interfaces.md).

## Running it securely

- Keep the web UIs (Tdarr's, Spoolman's, the AMS app's) on your LAN. Most of them have no login of their own,
  so anyone who can reach them can use them; put them behind a reverse proxy with access lists. Docker-published
  ports bypass host firewalls such as `ufw`.
- Tdarr can rewrite or delete anything in the media library: keep snapshots of it, read Tdarr's release notes
  before every upgrade, and test a new flow on a small library first.
- The GPU is given to Tdarr as a device reservation through the NVIDIA runtime, never by running it privileged.
- Secrets (logins, API keys, printer credentials) live only in each service's data, never in this repository or
  `.env`. Backups contain them: keep them private and off the host.
- Don't run an auto-updater such as Watchtower on these containers; upgrade by release instead.
- The policy check refuses privileged containers, added capabilities, host networking and Docker socket
  mounts unless a service documents why ([docs/security.md](docs/security.md)).

Report vulnerabilities privately: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
