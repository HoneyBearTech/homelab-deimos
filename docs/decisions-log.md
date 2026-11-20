# Decisions log

Decisions made while maintaining homelab-deimos, with the reason and the alternatives turned down.

## 2026-11-20: Renovate merges its own pull requests

Job `renovate-automerge` (#102).

**Decision.** Renovate merges its pull requests itself once every check passes,
majors included; new versions wait 3 days, security fixes and image rebuilds
don't, on the model homelab-atlas already uses. Kept manual: Tdarr and
qBittorrent stay switched off in Renovate. `platformAutomerge` is true.

**Why.** The owner asked for every repo with Renovate to merge its updates
automatically (2026-11-20) and chose this model, majors included, over keeping
majors manual.

**Not done.** Repository settings and branch rules (owner only) and workflows
are unchanged. A group merges automatically only when every update in it may, so
each manual exception gets its own pull request.

**Review findings.** Fixed: stale docs that still promised a manual review or
named Dependabot, and (where it applied) exceptions that sat inside the update
group. None declined. The upgrading guide no longer promises a manual review
before every merge.

## 2026-11-15: Renovate set-up (deimos-renovate, [#98](https://github.com/HoneyBearTech/homelab-deimos/pull/98))

- **Pre-commit hooks are tracked by regex managers, not Renovate's pre-commit manager.**
  - Why: a dry run of the built-in manager read the SHA as the version and ignored the `# vX.Y.Z` comment. It proposed bare tags with no digest, which would drop the SHA pins, and even downgrades such as gitleaks to v5.0.1. The regex manager reads the SHA as the digest and the comment as the version, so an update changes both.
  - Rejected: enabling the built-in manager; rewriting the hooks to bare tags.
- **Hook versions use a regex versioning, not `semver-coerced` or `loose`.**
  - Why: `semver-coerced` drops a fourth component, so shellcheck-py v0.11.0.1 would never see v0.11.0.2. `loose` counts all four components, but it treats `-rc` tags as stable. The regex counts all four components and marks `-…` suffixes as pre-releases.
- **Runner label major updates (`github-runners`) need Dependency Dashboard approval.**
  - Why: the pinned actionlint (1.7.12, its newest release) rejects `ubuntu-26.04`, so #93 can never pass.
  - Rejected: disabling runner updates entirely. They should come back once actionlint knows the label.
- **Updates wait 3 days after release (`minimumReleaseAge`), with security updates exempt.**
  - Why: a merge to `main` deploys to the host.
  - `minimumReleaseAgeBehaviour` is `timestamp-optional`, because GHCR images and `latest` digests report no release date and would otherwise be held forever. The accepted risk is that those updates skip the wait. Manual review and digest pinning still apply.
  - `internalChecksFilter` is `strict`, so no branch opens before the wait ends.
- **The GitLab CI mirror's images stay reviewed by hand**, as [dependencies.md](dependencies.md) already decides.
- **Review findings turned down:**
  - Remove the plan's "who's on it" list. Turned down: it names roles only.
  - The trailing "peeled commit" note on the gitleaks pin. Turned down: harmless, and the update leaves it in place. Remove it by hand when that bump lands.
- **Roadmap:** the remaining items can't be finished in the repository. They are the Tdarr login migration on the host, the rebuild rehearsal on a scratch machine with an NVIDIA GPU, and a read-only root for Tdarr, which depends on upstream. They stay open.
