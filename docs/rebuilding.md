# Rebuilding the host

How to bring the stack back on a new or wiped machine from a backup made by `scripts/backup.sh`
([upgrading.md](upgrading.md#backing-up)), with every service's settings and history as they were.

> **Planned:** the rebuild hasn't been rehearsed on a scratch machine yet ([roadmap](roadmap.md)).

You need: the backup directory (copied off the old host), **the age private key** it was encrypted to if the copies
off the host are encrypted (`BACKUP_AGE_RECIPIENT`), this repository, and access to the media library.

## An encrypted backup

A scheduled backup copied off the host with `BACKUP_AGE_RECIPIENT` set (its folder on the backup server holds only
`backup.tar.age` and `SHA256SUMS`) is opened on the machine that holds the age private key, never on the backup
server or the new host. Copy it there and check it, then decrypt it, which gives the backup directory as it was
written on the old host, and copy that to the new host once it is installed (step 1). It is then `~/restore` there,
the backup directory of steps 4 and 5; keep it private, it holds every login:

```sh
rsync -rlpt backup-host:/volume1/<folder>/<date>-<time>/ ~/sealed/
cd ~/sealed && sha256sum -c --quiet SHA256SUMS && cd -
(umask 077 && age -d -i deimos-backup.key ~/sealed/backup.tar.age | tar -x -C ~)   # ~/<date>-<time>/
rsync -rlpt ~/<date>-<time>/ <you>@<the new host>:restore/
rm -rf ~/sealed ~/<date>-<time>
```

`scripts/restore.sh` checks the backup's own `SHA256SUMS` again before it writes anything (step 5).

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

Install the NVIDIA driver (`sudo ubuntu-drivers install`, then reboot) and check it with `nvidia-smi`. After a
later driver update, the NVIDIA Container Toolkit's `nvidia-cdi-refresh` (step 3) regenerates the GPU's CDI spec by
itself.

## 3. Docker and the NVIDIA Container Toolkit

Install Docker Engine and the Compose plugin from Docker's own repository, following
[docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/) (Docker Engine 29.2 or
later, Compose 2.24 or later). Then the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
1.18 or later. Docker 29.2+ gives containers the GPU through CDI with the toolkit's `nvidia-cdi-hook` and the CDI
spec its `nvidia-cdi-refresh` keeps current; an older Engine uses the NVIDIA runtime hook, and Tdarr then loses the
GPU at every `systemctl daemon-reload` ([installing.md](installing.md#the-gpu-and-docker-292)).

```sh
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
nvidia-ctk cdi list                               # nvidia.com/gpu=all: the CDI spec Docker uses for the GPU
docker run --rm --gpus all ubuntu nvidia-smi      # the GPU is visible to containers
sudo usermod -aG docker "$USER"                   # equivalent to root on the host; log in again
```

Don't install an auto-updater such as Watchtower ([installing.md](installing.md#running-it-securely)).

## 4. The repository and settings

```sh
git clone https://github.com/HoneyBearTech/homelab-deimos.git && cd homelab-deimos
git checkout vX.Y.Z      # the backup's VERSION, or a newer release; verify it (verifying-releases.md)
cp /path/to/backup/env/.env .env && chmod 600 .env
```

A server that follows `main` ([upgrading.md](upgrading.md#following-main)) stays on `main` here instead
(skip the `git checkout`), and gets its pull agent back once the stack is healthy.

Copy any `<service>.env` from the backup's `env/` the same way (mode `600`). Edit `.env` if the new host's
paths differ (but keep the media library's container path; never point `CONFIG_ROOT` into `/var/lib/docker/volumes`),
create the apps' folders under `CONFIG_ROOT` as your user ([installing.md](installing.md#installing), step 3), and put
the Dozzle agent's certificate and key back from your password manager, owned by root
([installing.md](installing.md#dozzles-certificate)): they aren't in the backup, and without them the restore can't
create the agent's container. Then:

```sh
docker compose config --quiet && docker compose pull
```

## 5. Restore and start

```sh
scripts/restore.sh /path/to/backup    # verifies SHA256SUMS, creates the containers, asks first
docker compose up -d --wait           # waits until every service reports healthy
docker compose ps
```

With a GPU, check that Tdarr keeps it through a systemd reload (as apt and unattended-upgrades run one):

```sh
sudo systemctl daemon-reload
docker exec tdarr nvidia-smi -L       # still lists the GPU; "Failed to initialize NVML" means Docker is older than 29.2
```

Then sign in to each service: Tdarr should list its libraries, flows and history and see the GPU, and Spoolman
its spools.

## What the backup doesn't bring back

- **The media library**: it lives on its own storage, protected there.
- **The Dozzle agent's certificate and key** (`CONFIG_ROOT/dozzle/certs`): never backed up; they come from your
  password manager (step 4).
- **Host settings**: the static address, the GPU passthrough, the network share's mount, SSH keys, monitoring
  agents.
- **Other machines' view of this one**: reverse proxy hosts, DNS records, anything that reaches Deimos by
  address.
- **Services from other projects** on the same host: restore them from their own backups.
