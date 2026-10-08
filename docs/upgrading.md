# Upgrading

A homelab-deimos release changes which image versions run, and sometimes the services or settings. Services often
migrate their database when they start a new version and can't go back afterwards, so **every upgrade starts
with a backup**. The same steps apply to updating a checkout of `main`, which is possible but unsupported for
anything you depend on.

## Before you upgrade

1. Read the release notes (the `CHANGELOG.md` section) for every release between yours and the new one, and
   the services' own release notes, **always for Tdarr** and for any major version bump. Anything under
   **Upgrading** needs action.
2. Verify the new release ([verifying-releases.md](verifying-releases.md)).
3. Let Tdarr's queue drain, or pause it, so no transcode is interrupted halfway.

## Backing up

The state worth keeping is each service's data: Tdarr's database and settings, Spoolman's database and the AMS
app's data. `scripts/backup.sh` stops the stack so the databases are consistent, archives each of those mounts,
copies `.env` and any `<service>.env`, and starts again whatever was running:

```sh
scripts/backup.sh                       # into backups/<date>-<time>/ in the checkout (gitignored)
scripts/backup.sh /path/to/backup-dir   # or a directory of your choice (new or empty)
```

It **never** archives the media library, the transcode cache or Tdarr's logs: the library is far too large and
is protected by snapshots on the storage that holds it; the cache and logs are disposable.

The directory holds one `<service>--<path>.tar.gz` per mount, the settings under `env/`, a `MANIFEST` naming
each archive's service, container path, host path and image, and `SHA256SUMS`. Everything in it is readable
only by the user who ran the backup. **Copy it off the host.**

## Upgrading

```sh
git fetch --tags
git checkout vX.Y.Z
docker compose pull
docker compose up -d --wait
docker compose ps
```

Then check each service's web UI and logs (`docker compose logs <service>`) for migration errors, that Tdarr
still sees the GPU and its library, and run one small transcode before resuming the queue.

## Rolling back

If a service fails after the upgrade, go back to the previous version **and** restore its data; a service whose
database was migrated forward may not start with the older image. For one service (Spoolman here):

```sh
git checkout vPREVIOUS
scripts/restore.sh backups/YYYYMMDD-HHMMSS spoolman   # checks SHA256SUMS, lists what it replaces, asks first
docker compose up -d spoolman
```

`scripts/restore.sh` replaces everything in each of the service's mounts listed in the backup's `MANIFEST`,
keeping the files' owners and modes. Without service names it restores every service in the backup. Files
Tdarr already rewrote in the media library are not rolled back by this; restore them from the library's
snapshots.

## Restoring on a new host

See [rebuilding.md](rebuilding.md): install the host, restore `.env`, then `scripts/restore.sh` creates the
containers and fills their data from the backup.
