# Changelog

All notable changes to homelab-deimos are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Spoolman 0.22.1 → 0.27.0 (1,846 image-scan alerts → 16). Its database migrates forward on the first start, so
  back up first ([docs/upgrading.md](docs/upgrading.md)); a new web client is the default (`SPOOLMAN_LEGACY_CLIENT`
  brings the old one back), and cross-origin writes are refused. The image has no `curl` any more, so the health
  check uses its Python.

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

[Unreleased]: https://github.com/HoneyBearTech/homelab-deimos/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/HoneyBearTech/homelab-deimos/releases/tag/v0.1.0
