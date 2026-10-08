# Roadmap

Where homelab-deimos is going over roughly the next twelve months (from October 2026). Plans change; this
file changes with them, in the same pull request.

## Now: the stack in git

- The checks, backup and restore scripts, smoke test, release signing and project policies, ported from the
  sibling homelab stacks: backups skip the media library, and the smoke test runs without a GPU.
- `compose.yaml` with Tdarr, Spoolman and the AMS app, every image pinned by tag and digest for `linux/amd64`, a
  health check for each service, the GPU for Tdarr as an optional override, Dependabot proposing updates.
- The smoke test and the image scan running against the real stack in CI.
- First release (0.1.0) before Deimos switches over, so the server is first deployed from a signed, verified
  version.
- Switch Deimos to run the stack from a checkout of this repository, adopting the existing data, ports and
  library paths so nothing that reaches the server notices.

## Next: safe upgrades and rebuilds

- Rehearse the documented rebuild ([rebuilding.md](rebuilding.md)) on a scratch machine, including the GPU
  driver and the NVIDIA Container Toolkit.
- Scheduled backups copied off the host.

## Later

- Tighter container settings where the images allow it (read-only root filesystems, dropped capabilities,
  non-root users).
- Optional services as Compose profiles, so a smaller installation can leave them out.

## Security and project health

- Keep CI, CodeQL, Scorecard, dependency review and the DCO check green on every change.
- Branch protection on `main` with required checks; private vulnerability reporting; secret scanning with
  push protection.
- Reach the OpenSSF Best Practices **Passing** and **Silver** badges, and meet **OSPS Baseline** Levels 1
  and 2.
- Signed releases with checksums, SBOM and SLSA provenance from the first release on.

## What homelab-deimos will not do

- **Build or patch images.** It runs upstream images unchanged; bugs in the services go to their projects.
- **Configure the services' internals** (Tdarr libraries and flows, Spoolman's spools). Those are configured in
  each service and live in its data, which the backups cover.
- **Back up the media library.** It is too large; it belongs to the storage that holds it.
- **Store secrets.** No logins, tokens or keys in the repository, encrypted or not.
- **Auto-update.** Every version change is a reviewed commit.
- **Run the other services on the same host** (the local LLM stack, monitoring agents). They have their own
  setup, even where they share the host.
- **Be a general-purpose homelab distribution.** It describes one server; others are welcome to fork or borrow
  from it, but options that only another setup needs are out of scope.
