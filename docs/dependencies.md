# Dependencies and vulnerability management

How homelab-deimos chooses, obtains, tracks and updates what it's built from, and what happens when one of
those dependencies has a vulnerability.

homelab-deimos's dependencies are almost entirely the **container images** it runs. The rest are the tools its
checks and tests use and the GitHub Actions in its workflows. Its own code, the policy checker, uses only the
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
| Helper image for backups and the smoke test (busybox) | [`scripts/lib.sh`](../scripts/lib.sh) | version tag and digest | Docker |
| Linters and scanners used only by CI (actionlint, gitleaks, Trivy) | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`scan.yml`](../.github/workflows/scan.yml) | version tag and digest | Docker |
| GitHub Actions | [`.github/workflows/`](../.github/workflows/) | full commit SHA (version in a comment) | GitHub Actions |

Each release will carry a CycloneDX SBOM listing every service's image and digest
([verifying-releases.md](verifying-releases.md)).

## Tracking dependencies

- **Dependabot** ([`.github/dependabot.yml`](../.github/dependabot.yml)) checks weekly for new image
  versions in the Compose file, new tool versions and new Action versions, and opens a pull request for each.
  Dependabot alerts and security updates are on.
- The CI-only images in `run:` steps and the scripts' busybox image aren't seen by Dependabot; they're bumped
  by hand at least every quarter.
- **Patch and minor updates merge automatically** once every required check has passed (CI with the Compose
  policy check and the smoke test, CodeQL, dependency review), **except Tdarr's**: any Tdarr update is merged by
  hand, after reading its release notes, because a faulty Tdarr version can damage the media library.
- **Major updates are merged by hand**, after reading the service's release notes: a new major version can
  migrate its data one way.
- **A merge doesn't deploy.** The server runs what it last pulled; updates reach it when the operator pulls
  and redeploys, with a backup first ([upgrading.md](upgrading.md)).
- **Dependency review** blocks a pull request that adds or changes a Python or Actions dependency with a known
  vulnerability of moderate severity or higher, or a license outside the allowlist.
- **Nothing updates itself on the host.** Auto-updaters such as Watchtower are not used: they would run
  versions nobody reviewed.

## Policy for vulnerabilities in dependencies

Known vulnerabilities are found through Dependabot alerts, the services' and images' own advisories, and a
weekly scan of the pinned digests (Trivy, HIGH and CRITICAL findings that have a fix, for `linux/amd64`,
reported to code scanning). Each finding is triaged within 14 days:

1. **If a fixed version exists**, bump to it (a Dependabot pull request usually already does) and release.
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
what runs. **Updating them is the first step after the switch**, one service at a time, each with a backup first
([upgrading.md](upgrading.md)). Counts from the same scan of each project's current release (`linux/amd64`):

| Image | Today | Current release | What the update does |
| --- | --- | --- | --- |
| Spoolman | 0.22.1: 1,846 | 0.27.0: 16 | Almost all of 0.22.1's alerts are Debian packages of an 18-month-old base image. **Done on `main`** (deployed after the switch-over): the database migration was rehearsed from 0.22.1 data, and the AMS app's 1.1.1-dev build works with it |
| AMS app | 1.1.1-dev: 67 | 1.3.3: 11 | Moves from a development build to a release, and to the project's new name and image, HaspelSync (`ghcr.io/rdiger-36/haspelsync`); the old image name is being retired. **Done on `main`** (deployed after the switch-over): rehearsed from a legacy `printers.json` against Spoolman 0.27.0 |
| Tdarr | 2.86.01: 480 | 2.94.03: 478 | No change: the alerts are in Tdarr's bundled Node modules and binaries, which upstream hasn't updated. Updated by hand, after reading its release notes, since it rewrites the media library |

After the two updates the scan on `main` reported 99 open alerts for Spoolman, HaspelSync and the old AMS app
image, and 480 for Tdarr. **Not reachable in this stack** (73 alerts, dismissed in code scanning as "won't fix"
with this reason; which programs run was checked with `docker top` on the pinned images):

| Image | Package | Why it can't be reached |
| --- | --- | --- |
| Tdarr | `Tdarr_Server_Tray`, `Tdarr_Node_Tray` (44) | The desktop system-tray apps; the container runs only `Tdarr_Server`, `Tdarr_Node`, their Rust helpers and `exiftool` |
| Tdarr, HaspelSync | the npm command line's own modules (22: `brace-expansion`, `pacote`, `sigstore`, `tar`, …) | npm only builds the image; neither container ever runs it |
| Spoolman | `perl-base` (7) | Part of the Debian base for package-manager scripts; Spoolman (`entrypoint.sh`, then uvicorn) never runs Perl. Tdarr does run Perl (for `exiftool`), so its Perl alerts stay open |

**Open, waiting for upstream** (434: Tdarr 424, Spoolman 9, HaspelSync 1). No newer image has the fixed package
yet; each alert closes by itself when a bump to such an image is merged and the scan runs again. None of the
services is published beyond the LAN, and all three sit behind the homelab's reverse proxy with a LAN-only access
list. They're re-checked monthly.

- **Tdarr** (424): Debian packages of its base image (190, 16 of them critical, including Perl, which `exiftool`
  uses), and the Node modules and runtimes bundled into `Tdarr_Server` and `Tdarr_Node` (232). Tdarr is closed
  source, so only its maintainers can update these; its newest release (2.94.03) has as many. Reachable through
  the web UI and the node port on the LAN, and through crafted media files that Tdarr's FFmpeg and `exiftool`
  read.
- **Spoolman** (9): `anyio` (one critical), `urllib3` (its web stack and HTTP client), `libpcre2`, and `setuptools`
  and `msgpack` in a Python the image carries outside Spoolman's own environment (not located, so not dismissed).
  Reachable through its web UI and API on the LAN.
- **HaspelSync** (1): `proxy-addr` (critical) in its web framework, reachable through its web UI on the LAN (set
  its password, [installing.md](installing.md#running-it-securely)).
- **The old AMS app image** (58, category `trivy-bambulab-ams-spoolman-filamentstatus`): no longer in
  `compose.yaml`, so no scan updates them, but the server runs that image until it deploys the HaspelSync update.
  They're dismissed ("won't fix", replaced) once it has.

## Licenses

homelab-deimos's own files are MIT-licensed. The Python tools must be under an OSI-approved license that
dependency review allows (MIT, Apache-2.0, BSD, ISC, PSF, MPL-2.0 and similar); yamllint (GPL-3.0) is
allowed as a development tool. The images keep their own licenses (Tdarr's is proprietary freeware, see above).

## Policy for findings from static analysis (SAST)

CodeQL analyses the checker and the workflows on every pull request and weekly, and ruff runs the
bandit security rules in CI. A CodeQL finding of medium severity or higher is fixed before the next release, or,
if it is a false positive, dismissed in code scanning with a written reason. A ruff or shellcheck finding fails
CI; a deliberate exception is a per-line `noqa` or `shellcheck disable` with the reason next to it.
