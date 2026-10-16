# Upgrading

A homelab-deimos release changes which image versions run, and sometimes the services or settings. Services often
migrate their database when they start a new version and can't go back afterwards, so **every upgrade starts with a
backup**. The same steps apply to updating a checkout of `main` by hand; the maintainer's server follows `main`
automatically instead, where the merge is the upgrade ([Following `main`](#following-main)).

## Before you upgrade

1. Read the release notes (the `CHANGELOG.md` section) for every release between yours and the new one, and
   the services' own release notes, **always for Tdarr** and for any major version bump. Anything under
   **Upgrading** needs action. On the maintainer's server, Dependabot's major updates arrive automatically once the
   required checks pass, so their notes are read after they deploy.
2. Verify the new release ([verifying-releases.md](verifying-releases.md)).
3. Let Tdarr's queue drain, or pause it, so no transcode is interrupted halfway.

## Backing up

The state worth keeping is each service's data: Tdarr's database and settings, Spoolman's database and the AMS
app's data. `scripts/backup.sh` stops one service at a time, copies its mounts while it is stopped so its
database is consistent, and starts it again if it was running before compressing the copies; services with nothing
to archive keep running. It also copies `.env` and any `<service>.env`:

```sh
scripts/backup.sh                       # into backups/<date>-<time>/ in the checkout (gitignored)
scripts/backup.sh /path/to/backup-dir   # or a directory of your choice (new or empty)
```

If stopping a service fails while its container is still on its way down (a process stuck writing to a network
share can outlast Docker's stop), the backup waits up to `BACKUP_STOP_WAIT` seconds (default 120) for it to exit,
then carries on. If it still runs then, the backup fails, but starts the services again only after waiting once
more, so Compose doesn't skip a container that is about to exit. A backup that leaves any service down that was
running when it began fails and names it.

It **never** archives the media library, the transcode cache or Tdarr's logs: the library is far too large and
is protected by snapshots on the storage that holds it; the cache and logs are disposable.

On a server where the pull agent deploys `main` ([Following `main`](#following-main)), `scripts/backup.sh` and
`scripts/restore.sh` first wait for a deploy in progress to finish (printing "Waiting for a deploy by the pull agent
to finish"; after 30 minutes they give up with an error, before anything has stopped), then hold the agent's lock
until they exit. The agent skips its runs while they hold it, so it never starts or recreates a service that a backup
has stopped or a restore is rewriting, and deploys what it skipped at its next run after they finish. Where the agent
isn't installed (it keeps no state for the stack), nothing changes.

The directory holds one `<service>--<path>.tar.gz` per mount, the settings under `env/`, a `MANIFEST` naming
each archive's service, container path, host path and image, `NETWORKS`, `VERSION` (the checkout it was taken
with) and `SHA256SUMS`. Everything in it is readable only by the user who ran the backup. **Copy it off the
host.** [Scheduled backups](installing.md#scheduled-backups) do this nightly and copy each backup off the host.

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

If the release changed the systemd units in `deploy/systemd/` (the CHANGELOG says so), install them again as in
[installing.md](installing.md#scheduled-backups): copy them to `~/.config/systemd/user/` and run
`systemctl --user daemon-reload`.

## Following `main`

The maintainer's server doesn't check out releases: it runs `main`, and a small pull agent (kept in the
maintainer's private orchestration repository, not here) deploys every merge. A systemd user timer starts it about
every 90 seconds; when `main` has moved, it fast-forwards the checkout, runs `docker compose config --quiet`,
`docker compose pull` and `docker compose up -d --remove-orphans --wait`, and logs the old and the new commit. So
**a merge to `main` reaches the server within about two minutes**, including the updates Dependabot merges by
itself ([dependencies.md](dependencies.md#tracking-dependencies)).

- The agent only fast-forwards: it refuses a checkout with local changes or a `main` whose history was rewritten.
  If the configuration doesn't resolve or an image can't be pulled, it moves the checkout back, so the server keeps
  running what it ran.
- **The merge is the upgrade.** Before merging an update that can migrate a service's data, read the release notes
  and take a backup on the server (`scripts/backup.sh`). The updates that merge by themselves (patch, minor and
  major ones, and image rebuilds; never Tdarr's) rely on the nightly
  [scheduled backup](installing.md#scheduled-backups).
- **Backups and restores never overlap a deploy**: they take the agent's lock first ([Backing up](#backing-up)), so a
  merge that lands during the nightly backup deploys a few minutes after it, not halfway through.
- **Rolling back** is a revert pull request, deployed the same way. If the service already migrated its data,
  restore that too ([Rolling back](#rolling-back)).
- **Holding the server on a version**: check out that commit or release on the server (`git checkout vX.Y.Z`).
  The agent pauses while the checkout isn't on `main`, and carries on once it's switched back (`git switch main`).

## Rolling back

If a service fails after the upgrade, go back to the previous version **and** restore its data; a service whose
database was migrated forward may not start with the older image. For one service (Spoolman here):

```sh
git checkout vPREVIOUS
scripts/restore.sh backups/YYYYMMDD-HHMMSS spoolman   # checks SHA256SUMS, lists what it replaces, asks first
docker compose up -d spoolman
```

On a server that follows `main`, revert the change with a pull request instead of checking out the old release
(the merge deploys the old version), then restore the service's data the same way.

`scripts/restore.sh` replaces everything in each of the service's mounts listed in the backup's `MANIFEST`,
keeping the files' owners and modes. Without service names it restores every service in the backup. Files
Tdarr already rewrote in the media library are not rolled back by this; restore them from the library's
snapshots.

## Restoring on a new host

See [rebuilding.md](rebuilding.md): install the host, restore `.env`, then `scripts/restore.sh` creates the
containers and fills their data from the backup.
