# Quick start

You need a Linux host on amd64 (the reference is Ubuntu 24.04) with Docker Engine and the Compose v2 plugin, a
user in the `docker` group, and, for hardware transcoding, an NVIDIA GPU with its driver, the NVIDIA Container
Toolkit (1.18 or later) and Docker Engine 29.2 or later ([installing.md](installing.md#requirements)).

1. **Get the stack.**

   ```sh
   git clone https://github.com/HoneyBearTech/homelab-deimos.git && cd homelab-deimos
   ```

   Check out the latest release (`git checkout vX.Y.Z`, see the
   [releases](https://github.com/HoneyBearTech/homelab-deimos/releases)) and verify it first
   ([verifying-releases.md](verifying-releases.md)).

2. **Configure it.**

   ```sh
   cp .env.example .env && chmod 600 .env
   ```

   In `.env`, set `TZ`, `PUID`/`PGID` (a user that can read and write your media library), `MEDIA_PATH`,
   `CONFIG_ROOT` (where the apps keep their data; never inside `/var/lib/docker/volumes`),
   `SPOOLMAN_PUBLIC_URL` and `MEALIE_PUBLIC_URL`. With an NVIDIA GPU, uncomment `COMPOSE_FILE=compose.yaml:compose.gpu.yaml`. Every setting
   is described in [interfaces.md](interfaces.md#settings).

3. **Create the apps' folders under `CONFIG_ROOT`** as your user, so Docker doesn't create them owned by root
   ([where data lives](installing.md#where-data-lives)):

   ```sh
   . ./.env && mkdir -p "$CONFIG_ROOT"/tdarr/{config,server,logs,transcode} "$CONFIG_ROOT"/spoolman/config \
     "$CONFIG_ROOT"/haspelsync/{config,logs} "$CONFIG_ROOT"/mealie/config
   ```

   The Dozzle agent also needs the certificate and key it shares with your Dozzle hub, in
   `CONFIG_ROOT/dozzle/certs` and owned by root; it doesn't start without them
   ([installing.md](installing.md#dozzles-certificate) has the commands). Mealie needs a PostgreSQL database of its
   own on a database server, and its URL in `CONFIG_ROOT/mealie/secrets/database_url`; it doesn't start without it
   either ([installing.md](installing.md#mealies-database)).

4. **Check the GPU** is visible to containers (skip this without one):

   ```sh
   docker run --rm --gpus all ubuntu nvidia-smi
   ```

5. **Check and start.**

   ```sh
   docker compose config --quiet   # the file resolves with your settings
   docker compose up -d --wait     # waits until every service reports healthy
   docker compose ps
   ```

6. **Finish each service's setup in its web UI** (ports in [interfaces.md](interfaces.md#services-and-ports)).
   In Tdarr, add a library pointing at the media mount and test your flow on a small folder first: Tdarr
   replaces the files it processes. In the AMS app, add your printers (serial number, access code, address);
   it finds Spoolman on its own. In Mealie, sign in with its first account (`changeme@example.com`, `MyPassword`)
   and change its e-mail and password at once.

To upgrade later, follow [upgrading.md](upgrading.md); it starts with a backup.
