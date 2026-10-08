# Support

homelab-deimos is maintained by one person in their own time (see [GOVERNANCE.md](GOVERNANCE.md)), so this
is a best-effort policy, not a contract.

## Getting help

- **Questions, bugs and ideas**: [GitHub Issues](https://github.com/HoneyBearTech/homelab-deimos/issues).
  Say which version you run, which service is involved, and what you expected. Leave out tokens,
  passwords, hostnames, addresses and paths.
- **Problems inside a service** (Tdarr, Spoolman, the AMS app): that project's own
  support channels; homelab-deimos only pins and wires the images.
- **Security vulnerabilities**: never in a public issue; see [SECURITY.md](SECURITY.md).
- **Documentation**: the [README](README.md) and [docs/](docs/README.md), starting with the
  [quick start](docs/quick-start.md).

## Which versions are supported, and for how long

| Version | Supported with | Until |
| --- | --- | --- |
| The latest release | bug fixes and security fixes | the next release is published |
| An older release | nothing | it stopped being supported when the next release came out |
| `main` | bug fixes and security fixes | always (it's where fixes land first) |

- A fix is released as a new version (a patch release such as 0.1.1 for fixes only), never applied to an
  older release.
- **A release stops receiving security updates the moment the next release is published.** Its notes and
  [CHANGELOG.md](CHANGELOG.md) say what changed and whether upgrading needs steps; upgrading, with a backup
  first, is described in [docs/upgrading.md](docs/upgrading.md).
- Before 1.0, a minor release (0.2.0) may add, remove or rename services and settings; such changes are
  listed in the changelog under "Upgrading".
- The services themselves are supported by their own projects. A release pins the versions that were current
  and tested when it was made; when a service needs a security fix, it comes in a new release with the new
  image digest.
- If a supported line will end differently, it will be announced in the release notes and in this table
  first.
