# Dependencies and vulnerability management

How homelab-deimos chooses, obtains, tracks and updates what it's built from, and what happens when one of
those dependencies has a vulnerability.

homelab-deimos's dependencies are almost entirely the **container images** it runs. The rest are the tools its
checks and tests use and the GitHub Actions in its workflows. Its own code, the policy checker and torrent-watch, uses only the
Python standard library.

## Choosing a dependency

A new image or tool must:

- be open source under an OSI-approved license (the services' own licenses apply to them: homelab-deimos pins
  and runs them, it doesn't redistribute or link them);
- be actively maintained: releases in the last year, security issues answered, and **published for
  `linux/amd64`**, the host's architecture;
- come from the project itself, from its official registry; and
- be worth it: a new service needs a reason in the pull request that adds it.

**Exception: Tdarr.** Tdarr is free to use but not open source. It is accepted because nothing open source does
the same job as well; it runs with no added privileges, and its updates are always merged by hand after reading
its release notes.

## Obtaining dependencies

| Dependency | Declared in | Pinned by | Fetched by |
| --- | --- | --- | --- |
| The stack's images | [`compose.yaml`](../compose.yaml) | version tag and digest | `docker compose pull` |
| Check and test tools (pytest, coverage, ruff, yamllint, shellcheck) | [`requirements-dev.in`](../requirements-dev.in) → [`requirements-dev.txt`](../requirements-dev.txt) | exact version and SHA-256 hashes (`pip-compile --generate-hashes`) | `pip install --require-hashes --no-deps` |
| Helper image for backups and the smoke test (busybox) | [`scripts/lib.sh`](../scripts/lib.sh) | version tag and digest; Renovate regex manager | Docker |
| The smoke test's stand-in for Mealie's database server (PostgreSQL, the version the homelab's database server runs) | [`scripts/smoke-stack.sh`](../scripts/smoke-stack.sh) | version tag and digest; Renovate regex manager | Docker |
| Linters and scanners used only by GitHub CI (actionlint, gitleaks, Trivy) | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`scan.yml`](../.github/workflows/scan.yml) | version tag and digest; Renovate regex manager | Docker |
| GitLab CI images | [`.gitlab-ci.yml`](../.gitlab-ci.yml) | version tag and digest; reviewed by hand | Docker |
| GitHub Actions | [`.github/workflows/`](../.github/workflows/) | full commit SHA (version in a comment) | GitHub Actions |

Each release will carry a CycloneDX SBOM listing every service's image and digest
([verifying-releases.md](verifying-releases.md)).

## Tracking dependencies

- **Renovate** ([`renovate.json`](../renovate.json)) checks every Monday before 6am Central for Compose images,
  root Python requirements and GitHub Actions. Minor and patch updates are grouped into one pull request; major
  updates remain separate and carry the `major` label. All update pull requests require manual review and merge.
  CodeQL init, analyze and upload-sarif actions are grouped so they move to one shared version. The global label is
  `dependencies`; vulnerability alert pull requests carry `security`, and OSV vulnerability alerts are enabled.
- Narrow Renovate regex managers track the version and digest for busybox in `scripts/lib.sh`, the PostgreSQL
  smoke-test image in `scripts/smoke-stack.sh`, and actionlint, gitleaks and Trivy in GitHub workflow `run:` commands.
  GitLab CI images are pinned by version and digest but reviewed by hand. The Docker Hub `ubuntu` examples in
  rebuilding/quick-start docs are illustrative commands, not project automation or deployed images.
- **Tdarr and qBittorrent remain manual.** Renovate excludes both from image updates: Tdarr can rewrite the media
  library, and the LinuxServer `version-<qBittorrent>_v<libtorrent>` tag must not be parsed as an ordinary semver
  version. Read release notes and verify qBittorrent's libtorrent version before a manual change. The current
  `name:tag@sha256:<digest>` policy remains in force for Compose images; do not refresh an existing pin just for
  freshness. Any new pin needs upstream release/tag, official registry digest, `linux/amd64` manifest and consumer
  compatibility evidence.
- **Mealie's updates migrate a database outside this stack**, on its database server: keep nightly backups there,
  before any merge of one, since the change relies on them.
- **Every update pull request is reviewed and merged by hand**, including image rebuilds and major updates. A major
  update can migrate persistent data one way, so a revert alone might not undo it ([upgrading.md](upgrading.md)).
  Back up before a merge that changes a service image or data format.
- Dependabot alerts remain enabled. If Dependabot security-update pull requests remain enabled after this
  version-update migration, they also require manual review and merge; no auto-merge workflow remains.
- **A merge deploys.** The maintainer's server follows `main` with a pull agent, so a merge can reach the live host
  within minutes ([upgrading.md](upgrading.md#following-main)). Major updates can migrate persistent data; review release notes,
  migration guidance and backup readiness before merging.
- **Dependency review** blocks a pull request that adds or changes a Python or Actions dependency with a known
  vulnerability of moderate severity or higher, or a license outside the allowlist.
- **Nothing on the host updates from a registry.** Auto-updaters such as Watchtower are not used: they would run
  versions nobody reviewed. The pull agent only applies commits merged into `main`.

### qBittorrent

Two things about qBittorrent differ from the other images:

- **Its `version-` tag names the libtorrent too**: `version-5.3.0_v2.1.2` is qBittorrent 5.3.0 built on libtorrent
  2.1.2, pointing at the newest build of exactly that pair. Renovate does not update this image: its tag format must
  stay manual and must never be treated as ordinary semver. A new qBittorrent version is a hand-edited change: read its release
  notes (a new minor version can migrate the settings one way), check which libtorrent the build uses, and back up
  first. The plain version and `latest` move to whichever libtorrent linuxserver.io builds next, so a change to one
  could alter the libtorrent version unexpectedly.
- **libtorrent 2.1 or newer, never memory-mapped files.** libtorrent 2.0 maps every file it reads or writes into
  memory, which suits a network share badly: the share's page cache shows up as qBittorrent's memory, and every page
  fault waits on the network. libtorrent 2.1 reads and writes the files from a pool of threads, and that is what
  "Default" means for the disk I/O type on this build. The disk I/O type is a qBittorrent setting, not a Compose one
  ([installing.md](installing.md#qbittorrent)).

## Policy for vulnerabilities in dependencies

Known vulnerabilities are found through Dependabot alerts (kept enabled), Renovate OSV vulnerability alerts, the services' and images' own advisories, and a
weekly scan of the pinned digests (Trivy, HIGH and CRITICAL findings that have a fix, for `linux/amd64`,
reported to code scanning). Each finding is triaged within 14 days:

1. **If a fixed version exists**, bump to it (a Renovate pull request may already identify it) and release.
   A fix for an exploitable critical or high-severity vulnerability goes out in a patch release within 30
   days; others go out with the next release.
2. **If upstream hasn't released a fix**, assess whether it's reachable in this stack (which ports are
   published, and whether anything beyond the LAN can reach them). If it is, mitigate it where possible (for
   example, an access list or not publishing a port) and say so in the release notes; otherwise record the
   reason when dismissing the alert. Either way, it's fixed by a bump when upstream ships one.
3. **If an image is abandoned** and keeps accumulating vulnerabilities, replace it.

### Current findings

Triaged 8 October 2026, after the first image scan: 2,393 alerts, every one HIGH or CRITICAL (a few MEDIUM)
with a fixed package version somewhere upstream: Spoolman 1,846, Tdarr 480, the AMS app 67, autoheal and
socket-proxy none.

The images are pinned at the versions the server ran before it moved to this repository (Tdarr 2.86.01, Spoolman
0.22.1, the AMS app's 1.1.1-dev build), so that switching the server over changes how the services run but not
what runs. Updating them was the first step after the switch, one service at a time, each with a backup first
([upgrading.md](upgrading.md)): the server switched over on 13 October 2026 and moved to 0.2.0, with Spoolman
0.27.0 and HaspelSync 1.3.3, the same day. Counts from the same scan of each project's current release
(`linux/amd64`):

| Image | Today | Current release | What the update does |
| --- | --- | --- | --- |
| Spoolman | 0.22.1: 1,846 | 0.27.0: 16 | Almost all of 0.22.1's alerts are Debian packages of an 18-month-old base image. **Done in 0.2.0**, deployed 13 October 2026: the database migration was rehearsed from 0.22.1 data first |
| AMS app | 1.1.1-dev: 67 | 1.3.3: 11 | Moves from a development build to a release, and to the project's new name and image, HaspelSync (`ghcr.io/rdiger-36/haspelsync`); the old image name is being retired. **Done in 0.2.0**, deployed 13 October 2026: rehearsed from a legacy `printers.json` against Spoolman 0.27.0 first |
| Tdarr | 2.86.01: 480 | 2.94.03: 478 | No change: the alerts are in Tdarr's bundled Node modules and binaries, which upstream hasn't updated. Updated by hand, after reading its release notes, since it rewrites the media library: **2.96.01 in 0.2.1** (13 October 2026), for its safer file moves and copies on network shares; it left the open alerts unchanged |

After the two updates the scan on `main` reported 85 open alerts for Spoolman, HaspelSync and the old AMS app
image, and 480 for Tdarr. **Not reachable in this stack** (79 alerts, dismissed in code scanning as "won't fix"
with this reason; which programs run was checked with `docker top` on the pinned images, and again for Tdarr on
2.96.01):

| Image | Package | Why it can't be reached |
| --- | --- | --- |
| Tdarr | `Tdarr_Server_Tray`, `Tdarr_Node_Tray` (50) | The desktop system-tray apps; the container runs only `Tdarr_Server`, `Tdarr_Node`, their Rust helpers and `exiftool` |
| Tdarr, HaspelSync | the npm command line's own modules (22: `brace-expansion`, `pacote`, `sigstore`, `tar`, …) | npm only builds the image; neither container ever runs it |
| Spoolman | `perl-base` (7) | Part of the Debian base for package-manager scripts; Spoolman (`entrypoint.sh`, then uvicorn) never runs Perl. Tdarr does run Perl (for `exiftool`), so its Perl alerts stay open |

**Open, waiting for upstream** (434 on 8 October: Tdarr 424, Spoolman 9, HaspelSync 1; 481 after the scan of 12
October, Tdarr's up to 471 as advisories are published against the same bundled packages; 471 again after the move
to 2.96.01; 465 once that scan's six new tray-app alerts were dismissed on 13 October). No newer image has the fixed package yet; each alert closes by itself when a bump to such an image is
merged and the scan runs again. None of the services is published beyond the LAN, and all three sit behind the
homelab's reverse proxy with a LAN-only access list. They're re-checked monthly.

- **Tdarr** (424): Debian packages of its base image (190, 16 of them critical, including Perl, which `exiftool`
  uses), and the Node modules and runtimes bundled into `Tdarr_Server` and `Tdarr_Node` (232). Tdarr is closed
  source, so only its maintainers can update these; 2.96.01, the last 2.x release, has as many. Reachable through
  the web UI and the node port on the LAN, and through crafted media files that Tdarr's FFmpeg and `exiftool`
  read.
- **Spoolman** (9): `anyio` (one critical), `urllib3` (its web stack and HTTP client), `libpcre2`, and `setuptools`
  and `msgpack` in a Python the image carries outside Spoolman's own environment (not located, so not dismissed).
  Reachable through its web UI and API on the LAN.
- **HaspelSync** (1): `proxy-addr` (critical) in its web framework, reachable through its web UI on the LAN (set
  its password, [installing.md](installing.md#running-it-securely)).
- **The old AMS app image** (58, category `trivy-bambulab-ams-spoolman-filamentstatus`): dismissed as "won't fix"
  on 13 October 2026, once the server ran HaspelSync 1.3.3 in its place; no scan updates them, since the image is
  no longer in `compose.yaml`.

## Licenses

homelab-deimos's own files are MIT-licensed. The Python tools must be under an OSI-approved license that
dependency review allows (MIT, Apache-2.0, BSD, ISC, PSF, MPL-2.0 and similar); yamllint (GPL-3.0) is
allowed as a development tool. The images keep their own licenses (Tdarr's is proprietary freeware, see above).

## Policy for findings from static analysis (SAST)

CodeQL analyses the checker and the workflows on every pull request and weekly, and ruff runs the
bandit security rules in CI. A CodeQL finding of medium severity or higher is fixed before the next release, or,
if it is a false positive, dismissed in code scanning with a written reason. A ruff or shellcheck finding fails
CI; a deliberate exception is a per-line `noqa` or `shellcheck disable` with the reason next to it.
