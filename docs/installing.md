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

## Running it securely

- **Web UIs on the LAN only.** Tdarr, Spoolman and the AMS app mostly have no login of their own. Don't publish
  their ports beyond the LAN; Docker-published ports bypass host firewalls such as `ufw`, so restrict them at the
  router or with Docker's own `DOCKER-USER` rules, and use a reverse proxy's access lists for names you give them.
- **Turn on logins where a service offers them**, with long, unique passwords.
- **Protect the media library.** Tdarr replaces files in place. Keep snapshots on the storage that holds the
  library, check new flows on a small library first, and read Tdarr's release notes before upgrading it.
- **The GPU through the NVIDIA runtime only.** Tdarr gets the GPU as a device reservation; never run it
  privileged to reach the GPU.
- Keep `.env` at mode `600`; it holds no secrets by design, but it describes your host.
- Back up the services' data directories ([upgrading.md](upgrading.md#backing-up)).
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
