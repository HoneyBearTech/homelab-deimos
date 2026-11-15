# Plan: Renovate set-up and roadmap check

## What was asked

"Make sure that Renovate is set up properly on this repo and that all roadmap items are completed."

## What we found

- `renovate.json` is valid, and the Dependency Dashboard (#87) shows no errors. Renovate covers the Compose images,
  the GitHub Actions, `requirements-dev.txt` and the five pinned helper images. It keeps every digest pinned.
- **The pre-commit hooks are not covered.** `.pre-commit-config.yaml` is listed in `enabledManagers`, but Renovate's
  pre-commit manager is off by default and needs `"pre-commit": {"enabled": true}`. Because of this the hooks have
  fallen behind the rest of the repo: gitleaks is v8.29.1 here but v8.30.1 in CI, and ruff-pre-commit is v0.14.0
  but ruff 0.17.x elsewhere.
- **#93 (runner `ubuntu-24.04` → `26.04`) can never pass.** The pinned actionlint (1.7.12, still its newest
  release) rejects `ubuntu-26.04` as an unknown label. Renovate will keep proposing it.
- **#92 is red and partly overlaps #95.** #95 carries #92's Compose, script and Python updates and fixes the
  smoke-test database that made #92 fail. #92 also has three workflow bumps that #95 leaves out: Python 3.15 in
  `ci.yml` and `release.yml`, and Trivy 0.76.2 in `scan.yml`.
- `requirements-dev.txt` has 118 duplicated `--hash` lines, a side effect of in-place hash edits. pip accepts them,
  but the file no longer matches what `pip-compile` produces.
- Roadmap: the open items (Tdarr login on the host, the rebuild rehearsal on a scratch machine, a read-only root
  for Tdarr) can't be finished from the repository. Each one needs the maintainer, the host or an upstream change.

## What we'll change

1. **Turn on the pre-commit manager** in `renovate.json`. Check: a local Renovate extraction dry run lists all nine
   hook repositories and the node version, with their SHA pins and version comments.
2. **Hold runner image major bumps** (`ubuntu` runners) for approval on the Dependency Dashboard, so they come back
   only when actionlint knows the new label. Check: config validator passes and the dry run shows the rule applying.
3. **Add a three-day minimum release age** for updates, because a merge to `main` deploys to the host. Security
   updates are exempt. Check: config validator and dry run.
4. **Tighten the custom regex managers' file patterns** (anchored paths, escaped dots). Check: the dry run still
   finds all five helper-image pins.
5. **Regenerate `requirements-dev.txt` cleanly** with the documented `pip-compile --generate-hashes` command, at the
   same versions, to remove the duplicate hash lines. This step goes after #95 merges, because #95 changes the
   same file. Check: `make test` and `make lint` pass on a fresh hash-checked install.
6. **Update `docs/dependencies.md`** to describe the pre-commit coverage, the runner hold and the release-age rule.
   Check: the docs match `renovate.json`.
7. **Open pull requests**, each merged only with the maintainer's approval and green required checks:
   - #95 first. Before merging, confirm a current application backup and a current Mealie database backup, because
     the host follows `main`.
   - Then #92. Once #95 is in, Renovate rebases it down to the three workflow bumps, which we review then.
   - #93 is closed. The runner rule from step 2 brings it back when it can pass.
8. **Roadmap and notes.** Record this job in `docs/roadmap.md`, `docs/pass-map.md` and `docs/decisions-log.md`.
   The three host-side items stay open, with a short list of what the maintainer has to do for each.

## Who's on it

Researcher (done), Engineer, QA, Security Officer, independent Auditor.

## Risks

- Turning on the pre-commit manager will open a Renovate PR that bumps several hooks at once, including ruff 0.14 →
  0.17. It may need lint fixes, and it will be reviewed like any other update.
- Any merge to `main` deploys to the host. #95 can migrate Mealie's database, so backups come first.
- The release-age rule delays ordinary updates by three days.

## Out of scope

- Tdarr login on the host, the rebuild rehearsal and a Tdarr read-only root. These need the maintainer, the host or
  upstream. The steps the maintainer needs are listed in the job's report.
- The GitLab CI mirror's images stay reviewed by hand, as `docs/dependencies.md` already decides.
- Changing CI workflows by hand. Only Renovate's own workflow bumps in #92 touch them.
