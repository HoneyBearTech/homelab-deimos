# Assurance case

Why homelab-deimos meets its [security requirements](security.md): the threat model, the trust boundaries,
the secure design principles it follows, and how common weaknesses are countered.

## Threat model

| Asset | Threat | Countered by |
| --- | --- | --- |
| The host | A compromised or malicious image | Digest pins; versions change only by reviewed pull request; no privileged, capability, host-namespace or socket access without a reasoned label |
| The media library | A faulty Tdarr version or flow rewrites or deletes files | Tdarr updates read and merged by the maintainer, never auto-merged; flows tested on a small library first; snapshots on the library's storage (operator) |
| The media library | A backup or restore script touching it | Paths in a service's `org.honeybeartech.deimos.backup.skip` label are never archived by `backup.sh`, and `restore.sh` refuses to write them even if a backup lists them; the smoke test checks both |
| The web UIs | Someone on the LAN uses a service without logging in | LAN only, behind a reverse proxy's access lists; no ports published beyond the LAN ([installing.md](installing.md#running-it-securely)) |
| The host's devices | The GPU granted by running privileged | GPU only as a device reservation through the NVIDIA runtime; `privileged` refused by the policy check |
| Logins, printer credentials | Committed to the public repository | Kept in the services' data, never in the repo; `.gitignore`; GitHub push protection; gitleaks over the history in CI |
| Logins, printer credentials | Leaked through a backup | `scripts/backup.sh` writes backups readable only by the user who ran it; documented as secret, to be kept off the host |
| The services' data | An upgrade that migrates and breaks it | Backup before every upgrade; rollback = old tag + `scripts/restore.sh`, exercised by the CI smoke test |
| The services' data | A crafted backup writing outside the services' data | `restore.sh` verifies `SHA256SUMS` (which covers the `MANIFEST`), accepts only plain archive names and absolute container paths without `..`, and writes only a mount the service has read-write and doesn't exclude from backups |
| The release | Tampered release files | Keyless-signed `SHA256SUMS`, SLSA provenance, signed tags |
| The CI pipeline | Untrusted pull request input running with credentials | `pull_request` only, read-only token by default, untrusted values only via `env:`, actions pinned by SHA |
| Operator privacy | Hostnames, addresses, share names or paths in the public repo | Placeholders only; reviewed in every pull request |

Attackers considered: a compromised upstream image or registry tag; someone on the LAN reaching a web UI; a
malicious pull request; a tampered backup; an honest mistake in a Tdarr flow or upgrade. Out of scope: an
attacker who already has root or `docker` group access on the host, or write access to `.env`, the services'
data or the media library's storage.

## Trust boundaries

1. **Registries → host.** Images are trusted only at the digest a reviewed commit names.
2. **Repository → host.** The host runs a tagged, signed release or a reviewed `main`; it never pulls code
   that wasn't merged.
3. **LAN → web UIs.** Not published beyond the LAN; reached through a reverse proxy with access lists.
4. **Tdarr → media library.** Read-write by design; the boundary is the library's own snapshots.
5. **Containers → host.** Only each service's data directories, the media library and the GPU; no host
   namespaces. The one socket mount (socket-proxy, read-only, filtered, on an internal network) is a reviewed,
   labelled exception; autoheal reaches Docker only through it.
6. **Pull requests → CI.** Fork pull requests get a read-only token and no secrets.

## Secure design principles

- **Least privilege**: no added capabilities, no host namespaces, one filtered socket mount; the GPU as a device
  reservation; read-only CI tokens raised per job.
- **Fail-safe defaults**: the policy check fails on anything it doesn't recognise as allowed; an exception
  needs a reason, in the file, in review. `restore.sh` refuses anything in a backup it can't account for.
- **Complete mediation**: every change to what runs passes through a pull request and the same checks;
  nothing on the host updates itself.
- **Economy of mechanism**: one Compose file, one standard-library checker, upstream images unchanged.
- **Separation of privilege**: secrets live with the services, settings in `.env`, configuration in git.
- **Open design**: the whole configuration, policy and release process are public.

## Common weaknesses

| Weakness | Where it could arise | How it's countered |
| --- | --- | --- |
| CWE-494 (code downloaded without integrity check) | Image pulls | Digest pins; signed release checksums |
| CWE-798 / CWE-312 (hard-coded or cleartext credentials) | Compose `environment:`, `.env`, docs | No secrets in the repo; gitleaks; push protection |
| CWE-250 (unnecessary privileges) | Container settings, GPU access | Policy rules `privileged`, `cap-add`, `host-*`, `docker-socket`, `devices`; GPU by reservation |
| CWE-306 (missing authentication for critical function) | Web UIs without a login | LAN only, behind access lists; logins on where offered |
| CWE-22 (path traversal) | Restoring a backup | `restore.sh` validates archive names and mount paths from the checksummed `MANIFEST` |
| CWE-1104 (unmaintained third-party components) | Images, tools, Actions | Dependabot weekly; image scan; triage SLAs ([dependencies.md](dependencies.md)) |
| CWE-77/78 (injection) | Workflows, scripts | Untrusted values only via `env:`; actionlint and shellcheck; CodeQL for Actions |
| CWE-20 (improper input validation) | The checker's input | It reads JSON only with the standard library, never evaluates it, and exits 2 on anything that isn't a JSON object |

## Evidence

- CI on every change: ruff (with the bandit rules), yamllint, actionlint, gitleaks over the history,
  shellcheck, pytest with a 90 % branch-coverage floor, `docker compose config`, the policy check (with and
  without the GPU file), and a smoke test on amd64 that starts every pinned image without a GPU, waits for its health
  check, round-trips a backup and restore, and checks that excluded mounts are left alone (a dynamic test of the
  stack and the scripts).
- A weekly Trivy scan of every pinned image, and on every change to `compose.yaml`, into code scanning.
- CodeQL (Python and Actions) on every pull request and weekly; OpenSSF Scorecard weekly; dependency
  review on every pull request.
