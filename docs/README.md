# homelab-deimos documentation

Start with the [README](../README.md) for what homelab-deimos is and how to run it.

| Document | For |
| --- | --- |
| [Quick start](quick-start.md) | Getting the stack running on a fresh Docker host |
| [Installing](installing.md) | Host preparation (including the NVIDIA runtime), directory layout, running it securely, uninstalling |
| [Upgrading](upgrading.md) | Moving to a new release, backing up the services' data first, and rolling back |
| [Rebuilding](rebuilding.md) | Bringing the stack back on a new or wiped host from a backup |
| [Architecture](architecture.md) | The services, who talks to whom, and how updates flow from Dependabot to the host |
| [Interfaces](interfaces.md) | Every setting, port, volume, label and command |
| [Verifying releases](verifying-releases.md) | Checking that release files came from this repository, unchanged |
| [Security requirements](security.md) | What the stack protects, what it doesn't, and where secrets live |
| [Assurance case](assurance-case.md) | The threat model, trust boundaries and why the security requirements are met |
| [Dependencies](dependencies.md) | How images and tools are chosen, pinned, tracked and patched |
| [Roadmap](roadmap.md) | What's planned for the next year, and what homelab-deimos will not do |

Project policies live at the top of the repository: [CONTRIBUTING.md](../CONTRIBUTING.md),
[GOVERNANCE.md](../GOVERNANCE.md), [SECURITY.md](../SECURITY.md), [SUPPORT.md](../SUPPORT.md),
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) and [CHANGELOG.md](../CHANGELOG.md).

These documents change in the same pull request as the behaviour they describe. Anything not built yet is
marked **Planned**; for now that includes the stack itself (`compose.yaml`) and everything that runs it. The
scripts that check, back up and test it and the release workflow exist and run in CI. If you find a document
that's wrong, please [open an issue](https://github.com/HoneyBearTech/homelab-deimos/issues): it's treated as a bug.
