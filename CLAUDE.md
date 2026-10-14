# homelab-deimos

The Docker Compose stack for Deimos, the owner's homelab media-processing and 3D-printing server (Ubuntu 24.04,
amd64, a VM with an NVIDIA GPU passed through): Tdarr, Spoolman and the AMS app, every image pinned by tag and
digest so the server can be upgraded and rebuilt from this repository. Deimos runs the stack from a checkout of
`main` that a pull agent deploys (see Deploys below); its nightly backups go off the host.

## Before Making Structural Changes
Read the project's notes first. They live outside this repo, in the owner's Obsidian vault **Chronos** at
`~/Chronos/Projects/homelab-deimos/` (every file is prefixed `homelab-deimos-`):
- `homelab-deimos-roadmap.md`: phases with checkboxes, the owner's open questions, the OpenSSF phase and the
  owner's manual GitHub steps
- `homelab-deimos-Pass-Map.md`: pass-by-pass delivery log; add a row when a pass ships
- `homelab-deimos-Architecture.md`: the host, which containers run there today and which belong in this repo,
  data flow, deployment shape, cutover plan
- `homelab-deimos-Security-Considerations.md`: assets, threats, trust boundaries, checklist (Tdarr can rewrite
  or delete every file in the media library: treat these notes as load-bearing)
- `homelab-deimos-Decisions-Log.md`: ADR-style log (entries marked **Proposed** still need the owner's call)

Keep them current as work lands: tick roadmap checkboxes, add a Pass-Map row per pass, add dated
Decisions-Log entries. **The notes never go into this repo.** Chronos is versioned in its own private repo;
only commit or push it when the owner asks. The old in-repo vault path `.obsidian-docs/` stays gitignored.

The sibling repos (`~/GitHub/homelab-ares`, `-atlas`, `-helios`, `-hyperion`) follow the same pattern.
homelab-ares is the template for the homelab stacks: tooling that isn't specific to this host's services
(checker, backup scripts, smoke test, workflows) is kept the same as ares'; a change here that isn't about this
host's services goes to ares too (owner 2026-10-13).

## This repo is public
- No hostnames, IP addresses, internal or public domains, host paths, NFS exports, Portainer stack names or
  personal email addresses in anything committed: code, compose, docs, examples, tests, commit messages. Host
  facts live only in the Chronos notes. Examples use placeholders (`/srv/appdata`, `/srv/media`,
  `example.com`, `homelab-deimos_`).
- Commit as `31805425+HoneyBearTech@users.noreply.github.com` (set as this repo's `user.email`), with
  `git commit -s` for the DCO sign-off; commits and tags are SSH-signed.
- No secrets: the services' logins, API keys and any Bambu Lab account token or printer access code stay in
  their data on the host, never in `compose.yaml`, `.env` or the `*.example` files. A secret a service can only
  take from its environment gets its own gitignored `<service>.env` (`env_file`) with a committed
  `<service>.env.example`. `.gitignore` covers `.env`, keys, certificates, `appdata/`, `data/`, `media/`,
  `backups/`; extend it rather than work around it.
- Keep the repo on track for OpenSSF Baseline Levels 1 and 2 and the Best Practices Passing and Silver
  badges (project 15290). If a change would break a met criterion (for example unpinning an image or an
  Action, adding a workflow without `permissions:`, or dropping the coverage floor), say so before making it.

## Rules for the stack
- **Every image is pinned as `name:tag@sha256:<digest>`.** Never `latest`, never tag-only. Dependabot
  (`docker-compose` ecosystem) updates tag and digest together. Patch, minor and major updates (majors since owner
  2026-10-14), and image rebuilds (same tag, new digest; owner 2026-10-14), auto-merge once the required checks pass;
  every Tdarr update and any update Dependabot can't classify wait for the owner. Every merge deploys, the automatic
  ones included (see Deploys).
- **Amd64.** Every image must publish `linux/amd64`. The smoke test runs on `ubuntu-latest` and the image scan
  scans `linux/amd64`.
- **The GPU is optional to CI.** GitHub's runners have no GPU, so the NVIDIA device reservation must not stop
  the stack from starting without one: the reservation lives in `compose.gpu.yaml`, which the host adds through
  `COMPOSE_FILE` in its `.env` and CI leaves out (CI policy-checks both). GPU access is a device reservation,
  never `privileged: true` or a raw `devices:` mapping.
- **The media library is never backed up, restored or deleted by this repo's scripts.** It is far too large and
  is protected by the NAS. A service lists such paths in its `org.honeybeartech.deimos.backup.skip` label
  (comma-separated container paths); `backup.sh` skips them, `restore.sh` refuses to write them, and the smoke
  test checks both. Tdarr's lists `/media`, `/temp` and `/app/logs`; the AMS app's `/app/logs`. Tdarr writes to
  the library, so treat Tdarr upgrades and flow changes as risky to data: Tdarr is excluded from Dependabot
  auto-merge.
- `scripts/backup.sh` archives every read-write volume or bind mount except the Docker socket, anonymous volumes
  and the mounts a service's `backup.skip` label lists; paths inside a mount that its
  `org.honeybeartech.deimos.backup.exclude` label lists (none in use) are left out of the archive. Each service
  is stopped only while its data is copied; compression (pigz or gzip) runs after it starts again. `restore.sh`
  only writes mounts listed in the backup's MANIFEST that the service still mounts read-write, never a skipped
  mount, and leaves excluded paths and nested mounts in place. Keep it that way when adding a service.
- **Every service is hardened** (`x-hardened`: `cap_drop: [ALL]`, `no-new-privileges:true`) and adds back only
  capabilities from Docker's default set that its image needs, each with a comment saying why and proven by the
  smoke test; the root filesystem is read-only (`tmpfs` for what the image writes) wherever that works cleanly
  (docs/security.md#hardening; proposed 2026-10-13, pending the owner's review).
- **No privileged containers, capabilities beyond Docker's defaults, host network/PID, Docker socket mounts or
  device mappings** unless the service carries `org.honeybeartech.deimos.allow.<rule>: "<reason>"` and the owner
  agreed.
  `scripts/check_compose.py` enforces it in CI and in the release workflow. Exceptions in use (the owner asked for
  autoheal 2026-10-08, with the same exceptions as on ares): `socket-proxy` (`docker-socket`, read-only: filters
  the API down to list/inspect/restart/stop for autoheal, internal network, no port), `autoheal` (`latest`: the
  image's only maintained tag), `dozzle-agent` (`docker-socket`, read-only mount; an agent for the homelab's Dozzle
  hub on another host, port 7007, owner 2026-10-13). Don't add more without the owner agreeing.
- **Every service has a health check and the `autoheal: "true"` label** (except autoheal itself). autoheal must
  never get the socket itself, only `tcp://socket-proxy:2375`.
- **Never change the live server** (Deimos) without the owner asking: merges only as **Merging** says, no
  `docker compose up`, no edits to service data, Tdarr libraries or flows, or Portainer stacks. Read-only inspection
  (`docker ps`, `docker inspect`) only when asked.
- Every setting goes through `.env` (`${VAR:?…}` in compose when required) and is listed in `.env.example`
  and `docs/interfaces.md`.
- **Container paths and ports are load-bearing**: Tdarr stores library paths in its database, and the reverse
  proxy and Home Assistant reach the services by their published ports, and the Dozzle hub reaches the agent on
  port 7007. Adopt the existing data and keep paths and ports the same; see the cutover plan in Chronos.

## Deploys (owner's decision 2026-10-14)
- **A merge to `main` deploys.** A pull agent on Deimos (systemd user timer `homelab-pull@deimos`, from the owner's
  private homelab-ops repo; rolled out host by host, see Chronos) fetches `main` about every 90 s. When it moved,
  the agent fast-forwards the checkout and runs `docker compose config --quiet`, `docker compose pull` and
  `docker compose up -d --remove-orphans --wait`: a merge is live within about two minutes. It refuses local
  changes and non-fast-forward history, and moves the checkout back if config or pull fails.
- **Rollback is a revert PR**, deployed the same way. A revert doesn't undo a data migration: then the backup and
  `scripts/restore.sh` are the way back, with the owner.
- **Pinning by hand**: the owner checks out another commit or tag on the host; the agent pauses until the checkout
  is on `main` again. Release tags stay as signed, verified snapshots; they no longer drive deploys.

## Stack
- Docker Compose v2 (`compose.yaml` + `compose.gpu.yaml`, 6 services), upstream images: Tdarr (server with an
  internal node), Spoolman, the AMS app (HaspelSync, still under its old image name), autoheal + socket-proxy, a
  Dozzle agent (for the homelab's Dozzle hub on another host).
  Pinned at the versions the host ran before the cutover; updates follow it. Not in this repo: Open WebUI, Ollama,
  the Argus agent, the Portainer agent.
- Tooling (kept the same as homelab-ares'): `scripts/check_compose.py` (Python, standard library only:
  policy check + CycloneDX SBOM), `scripts/backup.sh`, `restore.sh`, `scheduled-backup.sh`, `smoke-test.sh`,
  `lib.sh` (bash, must run on macOS' bash 3.2); `scheduled-backup.sh` (run nightly by the systemd user units in
  `deploy/systemd/`) pushes with rsync to `BACKUP_REMOTE` and pings an Uptime Kuma push monitor, with its settings
  in the gitignored `backup.env` (read as data, never sourced), not `.env`, and needs GNU rsync and macOS'
  openrsync alike (no `--chmod`); ruff (`select = ["ALL"]`), yamllint, shellcheck, pytest + coverage (90 % branch floor),
  pip-tools for the hash-pinned `requirements-dev.txt`. CI-only: actionlint, gitleaks, CodeQL, Scorecard,
  dependency review, DCO, Trivy image scan, Dependabot auto-merge (patch/minor, rebuilds). CI logs in to Docker Hub with
  the read-only `DOCKERHUB_*` secrets when they exist (anonymous otherwise) and logs out before the smoke test
  starts a container (GOVERNANCE.md).
- GitLab copy: the owner's self-hosted GitLab (CE, LAN only; host facts in Chronos) is to hold a mirror, as for
  homelab-ares. GitHub stays the home (OpenSSF badges, Scorecard, Dependabot, releases); every change goes to
  GitHub as a PR, never to GitLab. `.gitlab-ci.yml` mirrors GitHub on a schedule (fast-forward only,
  `MIRROR_TOKEN` CI/CD variable) and re-runs the daemon-free checks, the policy check with and without
  `compose.gpu.yaml`; the smoke test stays GitHub-only. The owner still has to create the GitLab project, the
  `MIRROR_TOKEN` variable and the schedule: until then the file does nothing.
- Releases (`release.yml`, on a `v*.*.*` tag): policy check, source archive, CycloneDX SBOM,
  `SHA256SUMS` signed with cosign keyless, SLSA provenance, GitHub Release from the tag's `CHANGELOG.md`
  section. No images are built or published.

## Conventions
- `CHANGELOG.md` (Keep a Changelog): add to "Unreleased" with every user-visible change.
- Docs in `docs/` change in the same PR as the behaviour; anything not built yet is marked **Planned**.
- Workflows: top-level `permissions: contents: read` (Scorecard: `read-all`), raise per job; actions pinned by
  full SHA with a version comment; untrusted `${{ github.event.* }}` only through `env:`.
- Required checks in the `main` ruleset: `CI / Checks + tests`, `CI / Stack smoke test`, DCO sign-off,
  Dependency review, CodeQL's Analyze (python) / Analyze (actions). Don't rename those jobs.
- **Merging (owner's decision 2026-10-14):** Claude opens a PR for every change. A merge deploys (see Deploys),
  so Claude merges once every required check is green (the owner's standing permission, 2026-10-14) and
  then says that it merged and that it's deploying to Deimos. It asks first when the deploy needs something on
  the server (a new `.env` setting, a data migration without a recent good backup, an update this file says
  waits for the owner) or the server isn't healthy, and never turns on auto-merge (`gh pr merge --auto`)
  without the owner's OK. Never bypass a check or the ruleset.
  Dependabot's patch, minor and major updates (majors since owner 2026-10-14) and image rebuilds auto-merge through
  `dependabot-auto-merge.yml` once every required check passes, so they deploy unattended (the nightly backup and a
  revert PR are the way back; a one-way data migration needs a restore from the backup); every Tdarr update and any
  update Dependabot can't classify wait for the owner. Claude never merges a major update itself: majors are merged
  only by Dependabot's auto-merge or by the owner. Keeping that auto-merge is the owner's call: don't change
  `dependabot-auto-merge.yml` without it.

## Commands
```sh
make test     # checker tests + coverage floor (no Docker, no network)
make lint     # ruff check, ruff format --check, yamllint --strict, shellcheck -x
make check    # docker compose config --format json | scripts/check_compose.py  (needs .env and compose.yaml)
make smoke    # scripts/smoke-test.sh: throwaway project, healthy, backup/restore + scheduled backup (needs Docker)
make config   # docker compose config (resolved file)
```
Release (only when the owner asks): as in homelab-ares' CLAUDE.md: CHANGELOG section in a PR, then a signed
tag (`git tag -s vX.Y.Z`) checked against `.github/allowed_signers`, pushed; verify from outside afterwards.
