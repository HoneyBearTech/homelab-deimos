# Rebuilding the host

How to bring the stack back on a new or wiped machine from a backup made by `scripts/backup.sh`
([upgrading.md](upgrading.md#backing-up)), with every service's settings and history as they were.

> **Planned:** the rebuild hasn't been rehearsed on a scratch machine yet ([roadmap](roadmap.md)).

You need: the backup directory (copied off the old host), this repository, and access to the media library.

## 1. The operating system

Install a 64-bit Linux server; the reference is **Ubuntu 24.04 on amd64**. If it's a virtual machine, pass the
GPU through to it first. Then:

```sh
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades git
```

Give the machine the same address the old one had (or update everything that reaches it by address: reverse
proxy hosts, monitoring, anything that talks to Spoolman's API).

Mount the media library at the same host path as before (`MEDIA_PATH` in the backup's `.env`).

## 2. The GPU driver

Install the NVIDIA driver (`sudo ubuntu-drivers install`, then reboot) and check it with `nvidia-smi`.

## 3. Docker and the NVIDIA Container Toolkit

Install Docker Engine and the Compose plugin from Docker's own repository, following
[docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/) (Docker Engine 25 or
later, Compose 2.24 or later). Then the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html):

```sh
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
docker run --rm --gpus all ubuntu nvidia-smi      # the GPU is visible to containers
sudo usermod -aG docker "$USER"                   # equivalent to root on the host; log in again
```

Don't install an auto-updater such as Watchtower ([installing.md](installing.md#running-it-securely)).

## 4. The repository and settings

```sh
git clone https://github.com/HoneyBearTech/homelab-deimos.git && cd homelab-deimos
git checkout vX.Y.Z      # the release the backup was taken with, or newer; verify it (verifying-releases.md)
cp /path/to/backup/env/.env .env && chmod 600 .env
```

Copy any `<service>.env` from the backup's `env/` the same way (mode `600`). Edit `.env` if the new host's
paths differ (but keep the media library's container path), create the data directories as your user, then:

```sh
docker compose config --quiet && docker compose pull
```

## 5. Restore and start

```sh
scripts/restore.sh /path/to/backup    # verifies SHA256SUMS, creates the containers, asks first
docker compose up -d --wait           # waits until every service reports healthy
docker compose ps
```

Then sign in to each service: Tdarr should list its libraries, flows and history and see the GPU, and Spoolman
its spools.

## What the backup doesn't bring back

- **The media library**: it lives on its own storage, protected there.
- **Host settings**: the static address, the GPU passthrough, the network share's mount, SSH keys, monitoring
  agents.
- **Other machines' view of this one**: reverse proxy hosts, DNS records, anything that reaches Deimos by
  address.
- **Services from other projects** on the same host: restore them from their own backups.
