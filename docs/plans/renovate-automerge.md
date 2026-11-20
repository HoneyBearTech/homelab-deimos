# Plan: renovate-automerge

## What Sean asked for

"Ensure that all my repos that have renovate allow the renovate PRs to auto
merge", on the homelab-atlas model: every update, majors included, merges once
all checks pass, after a 3-day wait.

## What we found

- Renovate's config already has the 3-day wait and lets security fixes skip it;
  only `automerge` is off.
- Does a merge deploy? Yes: the pull agent applies `main`.

## What we'll change

1. `renovate.json`: turn on auto-merge for every update, majors included, the
   way homelab-atlas does it: `automerge: true`, squash merges,
   `platformAutomerge: true` (GitHub merges as soon as the required checks
   pass); new versions wait 3 days before they are proposed
   (`minimumReleaseAge`, with `timestamp-optional` so a registry without release
   dates doesn't hold an update forever); security fixes and image rebuilds
   (digest updates) skip the wait. Check: Renovate's config validator passes,
   and a local Renovate dry run shows auto-merge on for ordinary updates and off
   for the exceptions below.
2. Docs that say updates are merged by hand, or by Dependabot: README.md,
   GOVERNANCE.md, docs/dependencies.md, docs/architecture.md, docs/upgrading.md,
   docs/assurance-case.md. Say instead that Renovate merges updates once every
   check passes, and name the exceptions.
3. Record the job in `docs/` (pass map, decisions log, roadmap; created if
   missing).

## Kept manual

- Tdarr and qBittorrent stay switched off in Renovate (updated by hand), as
  today.

Each exception is an earlier decision by the owner; to drop one, say so on this
pull request.

## Who's on it

Engineer, Security Officer, Auditor.

## Risks

- A merge deploys: updates reach the server unattended, with the nightly backup
  behind them.
- Renovate's schedule is unchanged, so updates still arrive once a week.

## Out of scope

Repository settings and branch rules (only the owner changes those), and the
workflows under `.github/workflows` (a leftover Dependabot auto-merge workflow
stays as it is).
