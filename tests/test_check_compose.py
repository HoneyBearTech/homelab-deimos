import json
from pathlib import Path

import check_compose as cc
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DIGEST = "sha256:" + "a" * 64


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def rules(config: dict) -> set[tuple[str, str]]:
    return {(v.service, v.rule) for v in cc.check(config)}


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("nginx", cc.ImageRef("docker.io/library/nginx", None, None)),
        ("nginx:1.29", cc.ImageRef("docker.io/library/nginx", "1.29", None)),
        ("linuxserver/sonarr:4.0", cc.ImageRef("docker.io/linuxserver/sonarr", "4.0", None)),
        (f"lscr.io/linuxserver/radarr:6.0.4@{DIGEST}", cc.ImageRef("lscr.io/linuxserver/radarr", "6.0.4", DIGEST)),
        ("localhost:5000/app:1", cc.ImageRef("localhost:5000/app", "1", None)),
        ("localhost/app", cc.ImageRef("localhost/app", None, None)),
        (f"registry:5000/team/app@{DIGEST}", cc.ImageRef("registry:5000/team/app", None, DIGEST)),
    ],
)
def test_parse_image(ref: str, expected: cc.ImageRef) -> None:
    assert cc.parse_image(ref) == expected


def test_good_config_passes() -> None:
    assert cc.check(load("good.json")) == []


def test_every_rule_is_reported_exactly() -> None:
    assert rules(load("bad.json")) == {
        ("no-image", "image"),
        ("no-image", "build"),
        ("tag-only", "digest"),
        ("latest", "latest"),
        ("digest-only", "digest"),
        ("short-digest", "digest"),
        ("privileged", "privileged"),
        ("caps", "cap-add"),
        ("hostnet", "host-network"),
        ("hostpid", "host-pid"),
        ("socket", "docker-socket"),
        ("devices", "devices"),
        ("no-healthcheck", "healthcheck"),
        ("healthcheck-disabled", "healthcheck"),
        ("healthcheck-none", "healthcheck"),
        ("healthcheck-timing-only", "healthcheck"),
    }


def test_gpu_reservation_is_not_a_device_mapping() -> None:
    gpu = {"devices": [{"driver": "nvidia", "count": -1, "capabilities": [["gpu"]]}]}
    service = {
        "image": "ghcr.io/haveagitgat/tdarr:2.86.01@sha256:" + "a" * 64,
        "deploy": {"resources": {"reservations": gpu}},
        "healthcheck": {"test": ["CMD", "true"]},
    }
    assert cc.check({"services": {"tdarr": service}}) == []
    service["labels"] = {cc.ALLOW_LABEL + "devices": "needs the render node"}
    service["devices"] = ["/dev/dri:/dev/dri"]
    assert cc.check({"services": {"tdarr": service}}) == []


def test_allow_label_needs_a_reason() -> None:
    service = {"image": "app:latest", "labels": {cc.ALLOW_LABEL + "digest": "  "}, "healthcheck": {"test": ["CMD"]}}
    assert rules({"services": {"s": service}}) == {("s", "digest"), ("s", "latest")}
    service["labels"] = {cc.ALLOW_LABEL + "digest": "local test image", cc.ALLOW_LABEL + "latest": "same"}
    assert cc.check({"services": {"s": service}}) == []


def test_empty_config_has_no_violations() -> None:
    assert cc.check({}) == []
    assert cc.check({"services": None}) == []


def test_sbom_lists_pinned_images() -> None:
    bom = cc.sbom(load("good.json"))
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.6"
    assert bom["metadata"]["component"]["name"] == "deimos"
    [radarr, sonarr] = bom["components"]
    assert radarr["name"] == "lscr.io/linuxserver/radarr"
    assert radarr["version"] == "6.0.4"
    assert radarr["purl"] == (f"pkg:oci/radarr@sha256%3A{'a' * 64}?repository_url=lscr.io/linuxserver/radarr&tag=6.0.4")
    assert radarr["hashes"] == [{"alg": "SHA-256", "content": "a" * 64}]
    assert sonarr["properties"] == [{"name": "homelab-deimos:service", "value": "sonarr"}]


def test_sbom_is_reproducible_and_skips_services_without_images() -> None:
    config = load("bad.json")
    assert cc.sbom(config) == cc.sbom(load("bad.json"))
    names = [c["properties"][0]["value"] for c in cc.sbom(config)["components"]]
    assert "no-image" not in names
    tag_only = next(c for c in cc.sbom(config)["components"] if c["bom-ref"] == "service:tag-only")
    assert "purl" not in tag_only
    digest_only = next(c for c in cc.sbom(config)["components"] if c["bom-ref"] == "service:digest-only")
    assert digest_only["version"] == ""
    assert "tag=" not in digest_only["purl"]


def test_sbom_default_name() -> None:
    assert cc.sbom({})["metadata"]["component"]["name"] == "homelab-deimos"


def test_main_ok_with_sbom(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "sbom.json"
    assert cc.main([str(FIXTURES / "good.json"), "--sbom", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["components"]
    assert "2 service(s) checked, 0 violation(s)" in capsys.readouterr().out


def test_main_reports_violations(capsys: pytest.CaptureFixture[str]) -> None:
    assert cc.main([str(FIXTURES / "bad.json")]) == 1
    out = capsys.readouterr().out
    assert "socket: [docker-socket] mounts the Docker socket (/var/run/docker.sock)" in out
    assert f"allow with label {cc.ALLOW_LABEL}docker-socket" in out


def test_main_reads_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"services": {}}'))
    assert cc.main([]) == 0
    assert "0 service(s) checked" in capsys.readouterr().out


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_main_rejects_bad_input(tmp_path: Path, content: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    assert cc.main([str(path)]) == 2
    assert capsys.readouterr().err.startswith("error:")


def test_main_missing_file(tmp_path: Path) -> None:
    assert cc.main([str(tmp_path / "missing.json")]) == 2
