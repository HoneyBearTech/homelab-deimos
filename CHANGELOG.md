# Changelog

All notable changes to homelab-deimos are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- `scripts/backup.sh` and `scripts/restore.sh` no longer overlap a deploy on a server that follows `main` with the
  pull agent. Before anything stops, they wait for a deploy in progress to finish ("Waiting for a deploy by the pull
  agent to finish"; after 30 minutes they fail without having stopped anything),
  then hold the agent's lock until they exit, so the agent skips its runs meanwhile and deploys at its next run
  afterwards. Where the agent keeps no state for the stack, nothing changes
  ([docs/upgrading.md](docs/upgrading.md#backing-up)).
- Docs: the roadmap lists what's done (updates, hardening, pull deploys, the badges) and what's next (Tdarr 3, a
  rehearsed rebuild); the quick start no longer says releases don't exist yet; the docs that described automatic
  merges as patch and minor only now include majors and image rebuilds.
- Docs: the maintainer's server now follows `main` with a pull agent instead of running a release tag, so a merge
  deploys within about two minutes, Dependabot's automatic merges included. Rollback is a revert pull request; a
  server is held on a version by checking out that commit, which pauses the agent. Release tags stay as signed,
  verified snapshots ([docs/upgrading.md](docs/upgrading.md#following-main)).
- Dependabot's image rebuilds (the same tag moving to a new digest: a rebuild of the same app version, with newer
  base packages) now merge automatically once every required check passes, as on homelab-atlas. The rule merges only
  a pull request whose one changed file is `compose.yaml`, whose changed lines are all `image:` lines, and whose
  image tags stay the same. Tdarr's rebuilds wait, like all its updates.
- Dependabot's major updates now merge automatically too once every required check passes (owner's decision,
  14 October 2026), so they deploy to the maintainer's server unattended; the nightly backup is the way back from a
  one-way data migration. Every Tdarr update, and any update Dependabot can't classify, is still merged by hand.

## [0.3.0] - 2026-10-14

### Added

- Label `org.honeybeartech.deimos.backup.exclude`: paths inside a data mount (comma-separated, at any depth) that a
  backup leaves out of the mount's archive and a restore leaves in place. A path outside every data mount, or with
  `..` or a pattern character, fails the backup and the restore before anything stops. No service uses it yet;
  the `backup.skip` labels (Tdarr's `/media`, `/temp`, `/app/logs`, the AMS app's `/app/logs`) back up exactly
  what they did before.
- CI logs in to Docker Hub with a read-only token when the repository has one (`DOCKERHUB_USERNAME`,
  `DOCKERHUB_TOKEN`), so image pulls no longer hit the runners' shared anonymous limit; without it, CI pulls
  anonymously as before. The smoke test logs out before any container starts (GOVERNANCE.md).
- `.gitlab-ci.yml` for a mirror on the maintainer's self-hosted GitLab, which re-runs the checks that need no
  Docker daemon. It does nothing until that GitLab project, its `MIRROR_TOKEN` and a schedule exist.

### Changed

- `scripts/backup.sh` stops each service only while its data is copied, and compresses the copies after starting
  it again, with `pigz` on every core when it's installed (else `gzip`): Tdarr and the other services are down for
  a shorter time. Install pigz on the host (`docs/installing.md#scheduled-backups`). Services that Compose stopped
  along with the one being backed up (the AMS app, with Spoolman) are started again too, and the copy runs without
  a log driver, so Docker no longer writes the whole archive into the copying container's log.
- A mount inside another mount is archived on its own and left out of the outer mount's archive, and a restore of
  the outer mount leaves it in place. (No service mounts one inside another today.)
- The nightly backup runs in the best-effort I/O class at the lowest priority instead of the idle class, which
  could keep a stopped service waiting behind every other disk user on the host. **Re-install the systemd units**
  after upgrading (`docs/upgrading.md`); the timer stays at 04:00.
- The backup scripts, smoke test and workflows are kept the same as homelab-ares' (the template for the homelab
  stacks).
- docs/dependencies.md: six new alerts for Tdarr's tray apps are dismissed as not reachable (rechecked on
  2.96.01: the container never runs them); Tdarr has 465 open alerts.
- README: Dependabot's patch and minor updates merge on their own (Tdarr's and major updates wait for the
  maintainer); scheduled backups, `restore.sh` and `backup.env` are listed; the policy check's full rule list; the
  opening line names the services, matching the repository description.

### Security

- Every service drops every Linux capability and runs with `no-new-privileges` (also with `compose.gpu.yaml`).
  Tdarr adds back `CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID` and `KILL` for its init, which runs as root and
  starts the server and node as its own user; Spoolman adds back `SETUID` and `SETGID` to switch to its
  user. The AMS app now runs as `PUID`:`PGID` instead of root and adds back none, like the others. The table is
  in docs/security.md#hardening.
- Spoolman, the AMS app, the Dozzle agent and autoheal get a read-only root filesystem (`/tmp` in memory where
  they write it), like socket-proxy already had. Tdarr's stays writable. Spoolman's `PUID`/`PGID` must now stay
  1000, its image's user.
- The policy check enforces it: new rules `no-new-privileges` and `cap-drop`, and `cap-add` now fails only on a
  capability outside Docker's default set (the checker is the same as homelab-ares').
- **Upgrading:** take a backup first. Data with unexpected file owners can now fail with `EACCES` (permission
  denied): Tdarr's, Spoolman's and the AMS app's data must belong to `PUID`/`PGID`. The AMS app's
  directories belonged to root while it ran as root: hand them over before the upgrade
  (`sudo chown -R "$PUID:$PGID" "$AMS_PRINTERS_PATH" "$AMS_LOGS_PATH"`, with the values from `.env`), or it stays
  healthy but can't save its settings, its printers or its logs. A backup taken before the upgrade restores them
  as root's again. The hardening was tested on fresh, empty data only: after the upgrade check that Tdarr still
  sees the GPU, its libraries and its queue, and run one small transcode before resuming it; check Spoolman and
  the AMS app save changes. To go back, check out the previous release and `docker compose up -d`.

## [0.2.1] - 2026-10-13

### Changed

- Tdarr 2.86.01 → 2.96.01, the last 2.x release. It moves and copies files on network shares more reliably,
  noticing a failed write before a move deletes the source; keeps files in a library whose folder can't be read
  for the moment (a network outage) instead of dropping them; and copies finished files to NFS and SMB mounts
  more reliably. Its Rust engine, which Tdarr's servers already switched 2.86.01 to on 11 October 2026, is past
  its beta. No database migration or plugin changes. Back up and let the queue drain first
  ([docs/upgrading.md](docs/upgrading.md)). If the Rust engine misbehaves, `TDARR_ENGINE=nodejs` in the Tdarr
  service's environment brings the old one back (2.x only).

- docs/installing.md: HaspelSync behind a reverse proxy or under a DNS name needs that name under Settings →
  Allowed host names (it answers `403` until then), and its API answers only its web UI and callers with an API key.
- docs/dependencies.md: the image updates are deployed; the 58 alerts for the AMS app's old image are dismissed.

## [0.2.0] - 2026-10-13

### Added

- Scheduled backups: `scripts/scheduled-backup.sh` takes a backup, copies it off the host with rsync, keeps the
  newest few here and there, and reports to an Uptime Kuma push monitor. Settings in the optional, gitignored
  `backup.env` (template `backup.env.example`); systemd user units in `deploy/systemd/` run it nightly at 04:00
  (docs/installing.md#scheduled-backups). The smoke test runs it against a stand-in backup server.
- Each backup records `NETWORKS` (the address ranges of networks the stack joins but doesn't create) and `VERSION`
  (the checkout's `git describe`, the release to rebuild with).
- A Dozzle agent (`dozzle agent`, port 7007) for the homelab's Dozzle hub on another host, which shows every host's
  logs in one web UI, pinned at 11.3.2, the hub's version. It mounts the Docker socket read-only (a labelled policy
  exception: keep port 7007 on the LAN), has a health check and is restarted by autoheal. A `dozzle-agent`
  container started outside Compose has to be removed (`docker rm -f dozzle-agent`) before the first
  `docker compose up`.

### Changed

- `scripts/backup.sh` stops one service at a time, only while its own data is archived, instead of the whole stack;
  services with nothing to archive (autoheal, socket-proxy, the Dozzle agent) keep running.
- Spoolman 0.22.1 → 0.27.0 (1,846 image-scan alerts → 16). Its database migrates forward on the first start, so
  back up first ([docs/upgrading.md](docs/upgrading.md)); a new web client is the default (`SPOOLMAN_LEGACY_CLIENT`
  brings the old one back), and cross-origin writes are refused. The image has no `curl` any more, so the health
  check uses its Python.
- The AMS app 1.1.1-dev → HaspelSync 1.3.3, under the project's new image name `ghcr.io/rdiger-36/haspelsync`
  (67 image-scan alerts → 11); the container is now called `haspelsync`. It reads the existing printer list and
  logs a deprecation notice while `compose.yaml`'s environment variables still decide its settings (saving them
  once in its web UI moves them into `printers/settings.json`). It now books what a print used from the print's
  sliced file, fetched from the printer over FTPS (port 990); set a web UI password under Settings → Network
  access.

## [0.1.0] - 2026-10-08

The first release: the Deimos stack as a Compose file, every image pinned by version tag and digest for
`linux/amd64` at the versions the server runs today, with health checks, autoheal, a GPU override, backups that
leave the media library alone, and release signing around it.

### Added

- `compose.yaml`: Tdarr 2.86.01 (server with an internal node), Spoolman 0.22.1 and the AMS app (HaspelSync,
  1.1.1-dev build), each pinned by tag and digest for `linux/amd64` with a health check, at the versions the
  server runs today. The AMS app reaches Spoolman inside the stack. Tdarr's media library, transcode cache and
  logs and the AMS app's logs are excluded from backups.
- autoheal, behind a filtering socket proxy (read-only socket, internal network, only list, inspect, restart and
  stop): restarts any service whose health check fails, and can post a notice to a webhook in `autoheal.env`. The
  smoke test checks that it restarts an unhealthy container.
- `compose.gpu.yaml`: reserves the host's NVIDIA GPU for Tdarr; added on the host through `COMPOSE_FILE`, left
  out by CI. CI checks the policy with and without it.
- A `devices` policy rule: a service may not map host devices into its container (a GPU reservation is fine)
  unless an allow label gives the reason.
- Documentation: quick start, installing, upgrading, rebuilding, architecture, interfaces, security
  requirements, assurance case, dependencies, roadmap and verifying releases. Everything that depends on
  `compose.yaml`, which doesn't exist yet, is marked **Planned**.
- `.env.example` with the settings the stack is expected to read, and a `.gitignore` that keeps settings,
  service data, media, backups and keys out of the repository.
- Project policies (`SECURITY.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`),
  `CODEOWNERS`, issue and pull request templates.
- `scripts/check_compose.py`: the stack's policy check (every image pinned as `name:tag@sha256:<digest>`, no
  `latest`, no build, nothing privileged, no added capabilities, host network or PID namespace, no Docker
  socket mount, a health check on every service, unless a service's `org.honeybeartech.deimos.allow.<rule>`
  label gives the reason), and the CycloneDX SBOM of the images for releases; tests with a 90 % branch-coverage
  floor.
- `scripts/backup.sh` and `scripts/restore.sh`: back up every service's read-write data mounts and the
  settings files with a manifest and checksums (readable only by the user who ran it), and restore them after
  verifying the checksums and asking first. Paths in a service's `org.honeybeartech.deimos.backup.skip` label
  (the media library, caches, logs) are never archived, and a restore refuses to write them.
  `scripts/smoke-test.sh` starts the stack with throwaway settings, waits until every service is healthy, runs a
  backup and restore round trip and checks that excluded paths are left alone.
- CI on every change: ruff, yamllint, shellcheck, actionlint, gitleaks over the whole history, the checker's
  tests, and (once `compose.yaml` exists) the policy check and the smoke test on an amd64 runner. CodeQL,
  OpenSSF Scorecard, dependency review, a DCO check and a weekly image scan (Trivy, `linux/amd64`) also run.
- Dependabot for the images, the Python tools and the Actions; patch and minor updates merge automatically
  once every required check passes, except Tdarr's; major updates wait for the maintainer.
- A release workflow that publishes a source archive, the SBOM, `SHA256SUMS` signed keylessly with cosign,
  and SLSA build provenance ([docs/verifying-releases.md](docs/verifying-releases.md)).

[Unreleased]: https://github.com/HoneyBearTech/homelab-deimos/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/HoneyBearTech/homelab-deimos/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/HoneyBearTech/homelab-deimos/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/HoneyBearTech/homelab-deimos/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/HoneyBearTech/homelab-deimos/releases/tag/v0.1.0
