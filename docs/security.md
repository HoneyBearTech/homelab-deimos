# Security requirements

What homelab-deimos is meant to guarantee, what it leaves to the operator and the services, and where secrets
live. The reasoning behind these requirements is in the [assurance case](assurance-case.md).

> **Planned:** requirements 1 and 2 apply to `compose.yaml`, which isn't in the repository yet; the checker that
> enforces them already runs in CI and is tested. Requirements 3 to 6 apply now (5 from the first release).

## What homelab-deimos protects

1. **Only reviewed versions run.** Every image is pinned as `name:tag@sha256:<digest>`. A registry tag
   that is moved or hijacked doesn't change what `docker compose pull` fetches; a new version arrives only
   as a pull request that changes the digest.
2. **No container gets more of the host than it needs.** No service runs privileged, adds Linux
   capabilities, shares the host's network or PID namespace, or mounts the Docker socket (which is root on
   the host), unless the exception is written into the service as a reasoned label and reviewed. The GPU is
   granted as a device reservation through the NVIDIA runtime, not by privilege.
3. **No secrets in the repository.** The services keep their logins, API keys and any printer credentials in
   their own data, outside the repository. `.env` holds settings only. Secret scanning with push protection
   and a gitleaks scan of the whole history in CI back this up.
4. **No host details in the repository.** It is public: no hostnames, IP addresses, domains, share names or host
   paths are committed. Examples use placeholders.
5. **Releases are verifiable.** Release files are listed in a `SHA256SUMS` signed keylessly by the release
   workflow, with SLSA provenance and an SBOM of the pinned images ([verifying-releases.md](verifying-releases.md)).
6. **Backups don't leak and can't reach the media.** `scripts/backup.sh` writes archives readable only by the
   user who ran it, and `scripts/restore.sh` writes only the mounts the backup's checksummed `MANIFEST` lists and
   the service still has, never the media library or any other path a service excludes with its
   `org.honeybeartech.deimos.backup.skip` label.

## Policy

`scripts/check_compose.py` enforces requirements 1 and 2 on the resolved Compose file in CI and
before every release. A service may break a rule only with the label `org.honeybeartech.deimos.allow.<rule>` and
a non-empty reason:

| Rule | Fails when a service |
| --- | --- |
| `image` | has no `image` |
| `digest` | uses an image without both a tag and a `sha256` digest |
| `latest` | uses the `latest` tag |
| `build` | builds an image instead of pulling a pinned one |
| `privileged` | sets `privileged: true` |
| `cap-add` | adds Linux capabilities |
| `host-network` / `host-pid` | uses the host's network or PID namespace |
| `docker-socket` | mounts the Docker socket |
| `healthcheck` | has no health check, or disables it (one defined only in the image isn't visible to the check) |

## What it doesn't protect

- **The services themselves.** A vulnerability in Tdarr, Spoolman or the AMS app is that project's to fix;
  homelab-deimos ships the fixed version once it's released ([dependencies.md](dependencies.md)).
- **The media library.** Tdarr rewrites files in it by design. A bad flow or a faulty Tdarr version can damage
  or delete them; only snapshots on the storage that holds the library protect against that.
- **Access to the web UIs.** Most of these services have no login. Keeping them on the LAN, behind a reverse
  proxy's access lists, is the operator's job ([installing.md](installing.md#running-it-securely)).
- **The GPU driver and runtime.** The NVIDIA driver and Container Toolkit on the host are trusted and kept up to
  date by the operator.
- **The host.** Anyone with root, `docker` group membership or write access to `.env` or the services' data
  controls the stack; those are trusted.
- **Upstream images' internals.** Some images run as root inside the container; that is the image's design and
  is accepted where the image offers nothing else.

## Where secrets live

| Secret | Where | Never in |
| --- | --- | --- |
| Logins of services that have them | each service's data | the repository, `.env`, issues, logs you paste |
| Printer access codes or a printer cloud account token (if the AMS app needs one) | the AMS app's data, or its own gitignored `<service>.env` | same |
| Backups of the data | off the host, mode `600` | anywhere public |
