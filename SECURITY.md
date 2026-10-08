# Security Policy

## Reporting a vulnerability

Please report vulnerabilities in homelab-deimos through GitHub's private vulnerability reporting form:

https://github.com/HoneyBearTech/homelab-deimos/security/advisories/new

Do not report vulnerabilities through public issues. Include what you found, how to reproduce it, which
version or commit you tested, and what an attacker gains. Please avoid accessing or changing data that is
not yours while investigating. If you'd like to be credited under a particular name, or not at all, say
so.

## How reports are handled

This is a personal project maintained by one person (see [GOVERNANCE.md](GOVERNANCE.md)), so these are
targets rather than a contractual SLA:

1. **Acknowledge** the report within 7 days.
2. **Triage** it: reproduce the problem, decide whether it is a vulnerability in homelab-deimos (see "Scope"
   below) and agree its severity with you. If it isn't a vulnerability, you'll get an explanation, and the
   report may move to a public issue with your agreement.
3. **Fix** it privately in a [GitHub security advisory](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/about-repository-security-advisories),
   with a regression test where the problem can be tested, and invite you to review the fix if you want
   to.
4. **Release** the fix, then publish the advisory (requesting a CVE where it applies) with the affected
   and fixed versions and any workaround. The aim is to release fixes for critical and high-severity
   issues within 30 days of the report and others within 90 days, and to keep you updated at least every
   14 days until then.
5. **Credit** the reporter in the advisory and the release notes, unless you ask to stay anonymous.

## Coordinated disclosure

Please keep the details private until the advisory is published, or for 90 days after your report if no
fix has been released by then, whichever comes first. If you need a different timeline, say so in the
report.

## Supported versions

The latest release and `main` receive security fixes; a release stops receiving them when the next one is
published. Details are in [SUPPORT.md](SUPPORT.md).

## Published vulnerabilities

Vulnerabilities fixed in homelab-deimos are published as
[GitHub security advisories](https://github.com/HoneyBearTech/homelab-deimos/security/advisories) (with a CVE
where one applies), naming the affected and fixed versions, how to tell whether you're affected and how to
fix or work around it, and they're listed in the release notes and [CHANGELOG.md](CHANGELOG.md). None have
been reported so far.

Known vulnerabilities in the images the stack runs are handled as described in
[docs/dependencies.md](docs/dependencies.md).

## Scope

homelab-deimos is a Docker Compose stack: a version-pinned selection of upstream images (Tdarr, Spoolman
and the AMS app), how they're wired together, a policy checker and backup scripts. What it does and doesn't protect against
is described in [docs/security.md](docs/security.md) (its security requirements), and the reasoning in
[docs/assurance-case.md](docs/assurance-case.md) (threat model, trust boundaries and the defences against
common weaknesses).

In scope: anything that breaks the guarantees in those documents, for example a service in `compose.yaml`
that runs privileged, mounts the Docker socket or exposes a port it shouldn't without a documented reason,
an image that isn't pinned by digest, a release file that doesn't match its signature, a weakness in the
policy checker that lets such a service through, a backup or restore script that exposes or overwrites data it
shouldn't (including the media library, which it must never touch), or a committed file that leaks a secret. Not
vulnerabilities: anything that needs write access to the host, the `.env` file or the services' data
(they are trusted), and vulnerabilities in the services or images themselves, which belong with those
projects. Please tell us too if homelab-deimos's configuration makes one worse.
