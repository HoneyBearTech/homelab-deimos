# homelab-deimos

The Docker Compose stack for Deimos, the owner's homelab media-processing and 3D-printing server (Ubuntu 24.04,
amd64, a VM with an NVIDIA GPU passed through): Tdarr, Spoolman and the AMS app, every image pinned by tag and
digest so the server can be upgraded and rebuilt from this repository. The services still run from their old
setup on the host until the cutover (plan in Chronos).

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

The sibling repos `~/GitHub/homelab-ares` and `~/GitHub/homelab-atlas` follow the same pattern; ares is the
newest and the template for tooling, workflows and policy files.

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
  (`docker-compose` ecosystem) updates tag and digest together. Patch and minor updates auto-merge once the
  required checks pass; major updates wait for the owner. A merge never deploys: Deimos changes only on a
  deliberate pull.
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
- **No privileged containers, added capabilities, host network/PID, Docker socket mounts or device mappings** unless the
  service carries `org.honeybeartech.deimos.allow.<rule>: "<reason>"` and the owner agreed.
  `scripts/check_compose.py` enforces it in CI and in the release workflow. No exceptions are in use.
- **Every service has a health check** and the `autoheal: "true"` label (once autoheal is in the stack).
- **Never change the live server** (Deimos) without the owner asking: no `docker compose up`, no edits to
  service data, Tdarr libraries or flows, or Portainer stacks. Read-only inspection (`docker ps`,
  `docker inspect`) only when asked.
- Every setting goes through `.env` (`${VAR:?…}` in compose when required) and is listed in `.env.example`
  and `docs/interfaces.md`.
- **Container paths and ports are load-bearing**: Tdarr stores library paths in its database, and the reverse
  proxy and Home Assistant reach the services by their published ports. Adopt the existing data and keep paths
  and ports the same; see the cutover plan in Chronos.

## Stack
- Docker Compose v2 (`compose.yaml` + `compose.gpu.yaml`, 3 services), upstream images: Tdarr (server with an
  internal node), Spoolman, the AMS app (HaspelSync, still under its old image name). Pinned at the versions the
  host ran before the cutover; updates follow it. Not in this repo: Open WebUI, Ollama, the Argus agent, the
  Portainer agent.
- Tooling (ported from homelab-ares): `scripts/check_compose.py` (Python, standard library only:
  policy check + CycloneDX SBOM), `scripts/backup.sh`, `restore.sh`, `smoke-test.sh`, `lib.sh` (bash, must run
  on macOS' bash 3.2); ruff (`select = ["ALL"]`), yamllint, shellcheck, pytest + coverage (90 % branch floor),
  pip-tools for the hash-pinned `requirements-dev.txt`. CI-only: actionlint, gitleaks, CodeQL, Scorecard,
  dependency review, DCO, Trivy image scan, Dependabot auto-merge (patch/minor).
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
- Claude opens a PR for every change and may turn on auto-merge for it (`gh pr merge --auto --squash`), as on
  the sibling repos. Never bypass a check or the ruleset.

## Commands
```sh
make test     # checker tests + coverage floor (no Docker, no network)
make lint     # ruff check, ruff format --check, yamllint --strict, shellcheck -x
make check    # docker compose config --format json | scripts/check_compose.py  (needs .env and compose.yaml)
make smoke    # scripts/smoke-test.sh: throwaway project, healthy, backup/restore round trip (needs Docker)
make config   # docker compose config (resolved file)
```
Release (only when the owner asks): as in homelab-ares' CLAUDE.md: CHANGELOG section in a PR, then a signed
tag (`git tag -s vX.Y.Z`) checked against `.github/allowed_signers`, pushed; verify from outside afterwards.
