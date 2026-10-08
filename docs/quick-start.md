# Quick start

You need a Linux host on amd64 (the reference is Ubuntu 24.04) with Docker Engine and the Compose v2 plugin, a
user in the `docker` group, and, for hardware transcoding, an NVIDIA GPU with its driver and the NVIDIA Container
Toolkit ([installing.md](installing.md#requirements)).

1. **Get the stack.**

   ```sh
   git clone https://github.com/HoneyBearTech/homelab-deimos.git && cd homelab-deimos
   ```

   Once releases exist, check out the latest one (`git checkout vX.Y.Z`) and verify it first
   ([verifying-releases.md](verifying-releases.md)).

2. **Configure it.**

   ```sh
   cp .env.example .env && chmod 600 .env
   ```

   In `.env`, set `TZ`, `PUID`/`PGID` (a user that can read and write your media library), `MEDIA_PATH`, the
   data paths and `SPOOLMAN_PUBLIC_URL`. With an NVIDIA GPU, uncomment
   `COMPOSE_FILE=compose.yaml:compose.gpu.yaml`. Every setting is described in [interfaces.md](interfaces.md#settings).

3. **Create the data directories** as your user, so Docker doesn't create them owned by root:

   ```sh
   . ./.env && mkdir -p "$TDARR_SERVER_PATH" "$TDARR_CONFIGS_PATH" "$TDARR_LOGS_PATH" \
     "$TDARR_CACHE_PATH" "$SPOOLMAN_DATA_PATH" "$AMS_PRINTERS_PATH" "$AMS_LOGS_PATH"
   ```

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
   it finds Spoolman on its own.

To upgrade later, follow [upgrading.md](upgrading.md); it starts with a backup.
