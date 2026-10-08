## What and why

<!-- What does this change, and why? Link the issue it closes, if any. For an image bump, link the service's
release notes and say whether it migrates the service's data. -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`), certifying the [DCO](https://developercertificate.org/); see [CONTRIBUTING.md](../CONTRIBUTING.md#developer-certificate-of-origin).
- [ ] `make lint` and `make test` pass locally (CI runs the same, plus the workflow and secret scanners and the Compose policy check).
- [ ] Every image is pinned as `name:tag@sha256:<digest>`; any policy exception has an `org.honeybeartech.deimos.allow.<rule>` label with a reason.
- [ ] User-visible changes are in `CHANGELOG.md` under "Unreleased", and the README / `docs/` / `.env.example` are updated.
- [ ] No secrets, tokens, certificates, hostnames, domains, IP addresses or host paths are committed.
