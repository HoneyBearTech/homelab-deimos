import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
CONFIG_ROOT = "${CONFIG_ROOT:?set CONFIG_ROOT in .env}"
# Each app's data mounts: its folders under CONFIG_ROOT/<app> (the app's lowercase name, its container name) and the
# container path each is mounted at. The media library (MEDIA_PATH) isn't app data and stays a setting of its own.
APPS = {
    "tdarr": ("tdarr", {"server": "/app/server", "config": "/app/configs", "logs": "/app/logs", "transcode": "/temp"}),
    "spoolman": ("spoolman", {"config": "/home/app/.local/share/spoolman"}),
    "ams": ("haspelsync", {"config": "/app/printers", "logs": "/app/logs"}),
}
NOT_APP_DATA = ("${MEDIA_PATH:?set MEDIA_PATH in .env}:/media", "/var/run/docker.sock:/var/run/docker.sock:ro")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def compose() -> dict:
    return yaml.safe_load(read("compose.yaml"))


def test_every_setting_is_in_env_example_and_interfaces() -> None:
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", read("compose.yaml") + read("compose.gpu.yaml")))
    example = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", read(".env.example"), re.MULTILINE))
    table = re.findall(r"^\| (`[A-Z][^|]*) \|", read("docs/interfaces.md"), re.MULTILINE)
    documented = {name for cell in table for name in re.findall(r"`([A-Z][A-Z0-9_]*)`", cell)}
    assert used == example
    assert used <= documented


def test_each_app_keeps_its_data_in_folders_under_config_root() -> None:
    services = compose()["services"]
    for service, (app, folders) in APPS.items():
        assert services[service]["container_name"] == app
        mounts = [volume for volume in services[service]["volumes"] if volume not in NOT_APP_DATA]
        assert mounts == [f"{CONFIG_ROOT}/{app}/{folder}:{path}" for folder, path in folders.items()]
        assert "config" in folders


def test_no_other_service_mounts_app_data() -> None:
    for name, service in compose()["services"].items():
        if name not in APPS:
            assert all(volume in NOT_APP_DATA for volume in service.get("volumes") or []), name


def test_no_named_volumes() -> None:
    config = compose()
    assert "volumes" not in config
    for name, service in config["services"].items():
        for volume in service.get("volumes") or []:
            assert volume.startswith(("/", "${")), f"{name} mounts a named volume: {volume}"


def test_no_path_setting_points_into_docker_volumes() -> None:
    assert "/var/lib/docker" not in read("compose.yaml")
    for line in read(".env.example").splitlines():
        if not line.startswith("#"):
            assert "/var/lib/docker" not in line


def test_the_docs_warn_against_docker_volumes() -> None:
    for path in ("docs/installing.md", "CLAUDE.md"):
        text = " ".join(read(path).split())
        assert "/var/lib/docker/volumes" in text, path
        assert "docker volume prune" in text, path
