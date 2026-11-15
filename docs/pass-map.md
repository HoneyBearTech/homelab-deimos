# Pass map

Each pass of planned, reviewed work on homelab-deimos: when, what shipped or was found, and where.

| Date | Pass | What shipped or was found | Pull request |
| --- | --- | --- | --- |
| 2026-11-15 | deimos-renovate | Renovate config audit. The pre-commit hooks weren't tracked (the built-in manager is off by default and can't keep SHA pins), so they are now tracked by regex managers, along with their Node version. Runner major updates wait for Dependency Dashboard approval. Updates wait 3 days after release, with security updates exempt. The regex file patterns are anchored. The roadmap's open items need the host, a scratch machine or upstream. | [#96](https://github.com/HoneyBearTech/homelab-deimos/pull/96) |
