# Security requirements

What homelab-deimos is meant to guarantee, what it leaves to the operator and the services, and where secrets
live. The reasoning behind these requirements is in the [assurance case](assurance-case.md).

## What homelab-deimos protects

1. **Only reviewed versions run.** Every image is pinned as `name:tag@sha256:<digest>`. A registry tag
   that is moved or hijacked doesn't change what `docker compose pull` fetches; a new version arrives only
   as a pull request that changes the digest.
2. **No container gets more of the host than it needs.** Every service drops every Linux capability, adds back
   only the ones its image needs (never more than Docker's default set) and can't gain privileges from setuid
   binaries or file capabilities. No service runs privileged, shares the host's network or PID namespace, or
   mounts the Docker socket (which is root on the host), unless the exception is written into the service as a reasoned label and reviewed. The GPU is
   granted as a device reservation through the NVIDIA runtime, not by privilege.
3. **No secrets in the repository.** The services keep their logins, API keys and any printer credentials in
   their own data, outside the repository. `.env` holds settings only. Secret scanning with push protection
   and a gitleaks scan of the whole history in CI back this up.
4. **No host details in the repository.** It is public: no hostnames, IP addresses, domains, share names or host
   paths are committed. Examples use placeholders.
5. **Releases are verifiable.** Release files are listed in a `SHA256SUMS` signed keylessly by the release
   workflow, with SLSA provenance and an SBOM of the pinned images ([verifying-releases.md](verifying-releases.md)).
6. **Backups don't leak and can't reach the media.** `scripts/backup.sh` writes archives readable only by the
   user who ran it (the scheduled copy off the host keeps those modes), and `scripts/restore.sh` writes only the mounts the backup's checksummed `MANIFEST` lists and
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
| `no-new-privileges` | doesn't set `security_opt: [no-new-privileges:true]` |
| `cap-drop` | doesn't drop every capability (`cap_drop: [ALL]`) |
| `cap-add` | adds a capability outside Docker's default set (adding back one of those only narrows the default) |
| `host-network` / `host-pid` | uses the host's network or PID namespace |
| `docker-socket` | mounts the Docker socket |
| `devices` | maps a host device into the container (a GPU reservation under `deploy.resources` is allowed) |
| `healthcheck` | has no health check, or disables it (one defined only in the image isn't visible to the check) |

## Hardening

What each service runs with, beyond the policy. Every service has `cap_drop: [ALL]` and
`no-new-privileges:true` (in `compose.yaml`, so also with `compose.gpu.yaml`); the smoke test proves each one
still starts healthy and survives a backup and restore with these settings, with its data directories owned by
`PUID:PGID` as on the host. CI has no GPU, so Tdarr's GPU
transcoding with these settings is checked on the host, not in CI.

| Service | Capabilities added back | Read-only root filesystem | Why |
| --- | --- | --- | --- |
| tdarr | `CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID`, `KILL` | no | Its init (s6-overlay) runs as root: it takes ownership of its own directories (never `/media`), records that in a directory that then belongs to its user, starts the server and node as that user, and stops them on shutdown. The server and node run as that user with no capabilities, so their access to the media library is unchanged. Writable root: s6 runs its init from `/run`, and Tdarr updates its plugins and its own files in the image |
| spoolman | `SETUID`, `SETGID` | yes (`/tmp` in memory) | Its entrypoint starts as root and switches to its user. `PUID`/`PGID` must stay 1000 (its user's ids): for others it rewrites `/etc/passwd` |
| ams | none | yes (`/tmp` in memory) | Runs as `PUID`:`PGID` rather than its image's root, and writes only its printers and logs directories, which belong to that user |
| dozzle-agent | none | yes (`/tmp` in memory) | Only reads the socket and its certificate and key, all owned by root; writes its address file to `/tmp` |
| autoheal | none | yes | Only talks to socket-proxy |
| socket-proxy | none | yes (`/run`, `/tmp` in memory) | Only reads the socket |

The AMS app, the Dozzle agent, autoheal and socket-proxy run as root inside their containers, since their images
offer nothing else. Without capabilities that root can only reach what it owns (the AMS app keeps
`DAC_OVERRIDE` for its two directories).

## What it doesn't protect

- **The services themselves.** A vulnerability in Tdarr, Spoolman or the AMS app is that project's to fix;
  homelab-deimos ships the fixed version once it's released ([dependencies.md](dependencies.md)).
- **The media library.** Tdarr rewrites files in it by design. A bad flow or a faulty Tdarr version can damage
  or delete them; only snapshots on the storage that holds the library protect against that.
- **Access to the web UIs.** Most of these services have no login. HaspelSync (the AMS app) asks for a password
  only once one is set under Settings → Network access; set one, since without it anyone on the LAN can change the
  printer list and the Spoolman endpoint. Keeping them on the LAN, behind a reverse
  proxy's access lists, is the operator's job ([installing.md](installing.md#running-it-securely)).
- **The Docker API behind the proxy.** socket-proxy mounts the Docker socket (an allowed exception, labelled in
  `compose.yaml`) and lets autoheal only list, inspect, restart and stop containers; a flaw in the proxy would
  expose more. It has no published port and sits on an internal network that only autoheal joins.
- **Dozzle's access to Docker.** The Dozzle agent needs the Docker socket to read logs; read-only mounting
  doesn't limit the Docker API, so any Dozzle that the agent accepts, and anyone who can use that Dozzle's web UI,
  can see every container's logs. The agent accepts only a Dozzle that presents its own certificate and key, never
  the pair built into every Dozzle image ([installing.md](installing.md#dozzles-certificate)), so whoever holds the
  key (root on the hub's host and on every agent's, and the password manager it is kept in) is trusted too. It is an
  allowed exception (label in `compose.yaml`); keep its port on the LAN.
- **Backups off the host from the host itself.** The scheduled backup can delete old backups on the backup
  server, so whoever controls this host can delete or overwrite them too. Snapshots or a versioned copy on the
  backup server, outside this host's reach, are what protect them; a recycle bin may not see deletions made over
  rsync (Synology's doesn't).
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
| Printer access codes (in plain text in `printers.json`) | the AMS app's `/app/printers` | same |
| autoheal's webhook URL | `autoheal.env` (gitignored, mode `600`) | same |
| The Dozzle agent's key, shared with the Dozzle hub | `CONFIG_ROOT/dozzle/certs/dozzle_key.pem` (root, mode `600`) and a password manager | same, and the stack's backups |
| The backup push monitor's URL | `backup.env` (gitignored, mode `600`) | same |
| The key for the backup server | the backup user's `~/.ssh` on the host | same |
| Backups of the data | off the host, mode `600` | anywhere public |
