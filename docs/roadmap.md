# Roadmap

Where homelab-deimos is going over roughly the next twelve months (from October 2026). Plans change; this
file changes with them, in the same pull request.

## Done (October 2026)

- The stack in git: the checks, backup and restore scripts, smoke test, release signing and project policies;
  `compose.yaml` with Tdarr, Spoolman and the AMS app, every image pinned by tag and digest for `linux/amd64`, a health
  check for each service, the GPU for Tdarr in an optional override file; the smoke test running the real stack in CI.
- Deimos runs the stack from a checkout of this repository, adopting the existing data, ports and library paths.
  It first ran release 0.1.0; since 0.3.0 it follows `main` with a pull agent, so a merge deploys
  ([upgrading.md](upgrading.md#following-main)).
- The images brought up to their current releases, one service at a time: Spoolman and the AMS app (under its new
  image name, HaspelSync) in 0.2.0, Tdarr by hand in 0.2.1. Dependabot keeps them current since.
- Scheduled backups copied off the host (`scripts/scheduled-backup.sh`, a systemd timer), reported to Uptime Kuma.
- Tighter container settings (0.3.0): every service drops all capabilities and adds back only what its image needs,
  can't gain privileges, and runs with a read-only root filesystem where the image allows it (all but Tdarr); the AMS
  app runs as an unprivileged user.
- Backups and restores wait for a deploy by the pull agent and keep it from deploying while they run, so a merge
  never recreates a service a backup has stopped ([upgrading.md](upgrading.md#backing-up)).
- The OpenSSF Best Practices **Passing** and **Silver** badges, and **OSPS Baseline** Levels 1 and 2.

## Now

- Tdarr 3, by hand after reading its release notes (its engine changes); decide then whether to turn on Tdarr's
  own login.

## Next: safe upgrades and rebuilds

- Rehearse the documented rebuild ([rebuilding.md](rebuilding.md)) on a scratch machine, including the GPU
  driver and the NVIDIA Container Toolkit.

## Later

- A read-only root filesystem for Tdarr too, if its image ever allows one.
- Optional services as Compose profiles, so a smaller installation can leave them out.

## Security and project health

- Keep CI, CodeQL, Scorecard, dependency review and the DCO check green on every change.
- Branch protection on `main` with required checks; private vulnerability reporting; secret scanning with
  push protection.
- Keep the OpenSSF Best Practices Passing and Silver badges and OSPS Baseline Levels 1 and 2 as the project changes.
- Signed releases with checksums, SBOM and SLSA provenance, as named, verifiable snapshots of `main`.

## What homelab-deimos will not do

- **Build or patch images.** It runs upstream images unchanged; bugs in the services go to their projects.
- **Configure the services' internals** (Tdarr libraries and flows, Spoolman's spools). Those are configured in
  each service and live in its data, which the backups cover.
- **Back up the media library.** It is too large; it belongs to the storage that holds it.
- **Store secrets.** No logins, tokens or keys in the repository, encrypted or not.
- **Auto-update from registries.** Every version change is a commit in `main` that passed CI; the server applies
  only those.
- **Run the other services on the same host** (the local LLM stack, monitoring agents). They have their own
  setup, even where they share the host.
- **Be a general-purpose homelab distribution.** It describes one server; others are welcome to fork or borrow
  from it, but options that only another setup needs are out of scope.
