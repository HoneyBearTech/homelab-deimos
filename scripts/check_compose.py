"""Check the resolved Compose config against the stack's policy, and describe its images as an SBOM.

Reads the output of ``docker compose config --format json`` (from a file or stdin) and reports every
service that breaks a rule. A service may break a rule on purpose only when it carries the label
``org.honeybeartech.deimos.allow.<rule>`` with the reason as its value. Rules:

- ``image``: the service has an ``image``.
- ``digest``: the image is pinned by tag and digest (``name:tag@sha256:<64 hex>``).
- ``latest``: the tag isn't ``latest``.
- ``build``: the service doesn't build an image from source.
- ``privileged``: no ``privileged: true``.
- ``cap-add``: no added Linux capabilities.
- ``host-network`` / ``host-pid``: no host network or PID namespace.
- ``docker-socket``: no Docker socket mount (it is root on the host).
- ``healthcheck``: the service defines a health check (a check only the image defines isn't visible here).

With ``--sbom FILE`` it also writes a CycloneDX 1.6 JSON SBOM listing each service's image, for releases.
Exit codes: 0 = no violations, 1 = violations, 2 = unreadable input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, NamedTuple

ALLOW_LABEL = "org.honeybeartech.deimos.allow."
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SBOM_NAMESPACE = uuid.UUID("6f1f6c55-4b8e-4f0a-9a57-1d1f4d2b7a10")

type Service = dict[str, Any]


class ImageRef(NamedTuple):
    """An image reference split into repository (with registry), tag and digest."""

    repository: str
    tag: str | None
    digest: str | None


class Violation(NamedTuple):
    """One rule a service breaks."""

    service: str
    rule: str
    detail: str


def parse_image(ref: str) -> ImageRef:
    """Split ``registry/name:tag@sha256:…`` and fill in Docker Hub's implied registry and namespace."""
    name, _, digest = ref.partition("@")
    tag = None
    slash = name.rfind("/")
    colon = name.rfind(":")
    if colon > slash:
        name, tag = name[:colon], name[colon + 1 :]
    first, _, rest = name.partition("/")
    if not rest:
        name = f"docker.io/library/{first}"
    elif "." not in first and ":" not in first and first != "localhost":
        name = f"docker.io/{name}"
    return ImageRef(name, tag, digest or None)


def allowed(service: Service, rule: str) -> bool:
    """Whether the service opts out of a rule with a non-empty reason label."""
    labels = service.get("labels") or {}
    return bool(str(labels.get(ALLOW_LABEL + rule, "")).strip())


def image_violations(service: Service) -> list[tuple[str, str]]:
    """Return the image rules this service breaks, as (rule, detail) pairs."""
    image = service.get("image")
    if not image:
        return [("image", "no image")]
    ref = parse_image(image)
    found = []
    if ref.tag is None or ref.digest is None or not DIGEST_RE.match(ref.digest):
        found.append(("digest", f"{image} is not pinned as name:tag@sha256:<digest>"))
    if ref.tag == "latest":
        found.append(("latest", f"{image} uses the latest tag"))
    return found


def runtime_violations(service: Service) -> list[tuple[str, str]]:
    """Return the privilege and isolation rules this service breaks, as (rule, detail) pairs."""
    found = []
    if service.get("build"):
        found.append(("build", "builds an image instead of pulling a pinned one"))
    if service.get("privileged"):
        found.append(("privileged", "runs privileged"))
    if service.get("cap_add"):
        found.append(("cap-add", "adds capabilities: " + ", ".join(service["cap_add"])))
    if service.get("network_mode") == "host":
        found.append(("host-network", "uses the host network"))
    if service.get("pid") == "host":
        found.append(("host-pid", "uses the host PID namespace"))
    for volume in service.get("volumes") or []:
        source = str(volume.get("source", ""))
        if source.endswith("docker.sock"):
            found.append(("docker-socket", f"mounts the Docker socket ({source})"))
    healthcheck = service.get("healthcheck") or {}
    test = healthcheck.get("test") or []
    if healthcheck.get("disable") or not test or test[0] == "NONE":
        found.append(("healthcheck", "has no health check"))
    return found


def check(config: dict[str, Any]) -> list[Violation]:
    """Every violation in a resolved Compose config, sorted by service."""
    violations = []
    for name, service in sorted((config.get("services") or {}).items()):
        for rule, detail in image_violations(service) + runtime_violations(service):
            if not allowed(service, rule):
                violations.append(Violation(name, rule, detail))
    return violations


def purl(ref: ImageRef) -> str:
    """Return the package URL of a pinned image (``pkg:oci/…``)."""
    name = ref.repository.rsplit("/", 1)[-1].lower()
    digest = (ref.digest or "").replace(":", "%3A")
    qualifiers = f"repository_url={ref.repository}"
    if ref.tag:
        qualifiers += f"&tag={ref.tag}"
    return f"pkg:oci/{name}@{digest}?{qualifiers}"


def sbom(config: dict[str, Any]) -> dict[str, Any]:
    """Build a CycloneDX 1.6 SBOM, one container component per service; the same input gives the same bytes."""
    components = []
    for name, service in sorted((config.get("services") or {}).items()):
        image = service.get("image")
        if not image:
            continue
        ref = parse_image(image)
        component: dict[str, Any] = {
            "type": "container",
            "bom-ref": f"service:{name}",
            "name": ref.repository,
            "version": ref.tag or "",
            "properties": [{"name": "homelab-deimos:service", "value": name}],
        }
        if ref.digest:
            component["purl"] = purl(ref)
            component["hashes"] = [{"alg": "SHA-256", "content": ref.digest.removeprefix("sha256:")}]
        components.append(component)
    project = str(config.get("name") or "homelab-deimos")
    serial = uuid.uuid5(SBOM_NAMESPACE, json.dumps(components, sort_keys=True))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {"component": {"type": "application", "bom-ref": "stack", "name": project}},
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the check (and write the SBOM if asked); return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("config", nargs="?", help="output of `docker compose config --format json` (default: stdin)")
    parser.add_argument("--sbom", metavar="FILE", help="also write a CycloneDX SBOM of the images to FILE")
    args = parser.parse_args(argv)

    try:
        text = Path(args.config).read_text(encoding="utf-8") if args.config else sys.stdin.read()
        config = json.loads(text)
    except (OSError, json.JSONDecodeError) as err:
        print(f"error: can't read the Compose config: {err}", file=sys.stderr)
        return 2
    if not isinstance(config, dict):
        print("error: the Compose config is not a JSON object", file=sys.stderr)
        return 2

    if args.sbom:
        Path(args.sbom).write_text(json.dumps(sbom(config), indent=2) + "\n", encoding="utf-8")

    violations = check(config)
    for v in violations:
        print(f"{v.service}: [{v.rule}] {v.detail} (allow with label {ALLOW_LABEL}{v.rule}: <reason>)")
    count = len(config.get("services") or {})
    print(f"{count} service(s) checked, {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
