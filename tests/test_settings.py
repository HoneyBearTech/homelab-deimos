import hashlib
import re
from pathlib import Path

import torrent_watch
import yaml

ROOT = Path(__file__).parent.parent
CONFIG_ROOT = "${CONFIG_ROOT:?set CONFIG_ROOT in .env}"
# Each app's data mounts: its folders under CONFIG_ROOT/<app> (the app's lowercase name, its container name) and the
# container path each is mounted at. The media library (MEDIA_PATH) and the torrents' files (TORRENTS_PATH) aren't app
# data and stay settings of their own.
APPS = {
    "tdarr": ("tdarr", {"server": "/app/server", "config": "/app/configs", "logs": "/app/logs", "transcode": "/temp"}),
    "spoolman": ("spoolman", {"config": "/home/app/.local/share/spoolman"}),
    "ams": ("haspelsync", {"config": "/app/printers", "logs": "/app/logs"}),
    "mealie": ("mealie", {"config": "/app/data"}),
    "qbittorrent-direct": ("qbittorrent-direct", {"config": "/config"}),
    "torrent-watch": ("torrent-watch", {"config": "/config"}),
}
NOT_APP_DATA = (
    "${MEDIA_PATH:?set MEDIA_PATH in .env}:/media",
    "${TORRENTS_PATH:?set TORRENTS_PATH in .env}:/downloads",
    "/var/run/docker.sock:/var/run/docker.sock:ro",
)


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


def test_every_env_file_is_optional_gitignored_and_has_a_committed_template() -> None:
    ignored = read(".gitignore").splitlines()
    for name, service in compose()["services"].items():
        for entry in service.get("env_file") or []:
            assert isinstance(entry, dict), f"{name}: {entry} must be optional (path + required: false)"
            path = entry["path"]
            assert entry.get("required") is False, f"{name}: {path} must not be required"
            assert path.endswith(".env"), f"{name}: {path} isn't a <service>.env"
            assert "*.env" in ignored, f"{name}: {path} isn't gitignored"
            assert (ROOT / f"{path}.example").is_file(), f"{name}: {path}.example is missing"


def test_qbittorrent_web_ui_has_the_same_port_on_both_sides() -> None:
    # It answers "Unauthorized" to a request whose Host header names a port other than its own.
    service = compose()["services"]["qbittorrent-direct"]
    port = service["environment"]["WEBUI_PORT"]
    assert f"{port}:{port}" in service["ports"]


def test_torrent_watch_is_recreated_when_its_script_changes() -> None:
    # Compose keeps a container when only a file mounted into it changes, and a pull replaces that file, which leaves
    # the running container's mount pointing at nothing. Changing this setting along with the script recreates it.
    service = compose()["services"]["torrent-watch"]
    digest = hashlib.sha256((ROOT / "scripts/torrent_watch.py").read_bytes()).hexdigest()
    assert service["environment"]["TORRENT_WATCH_SCRIPT_SHA256"] == digest


def test_torrent_watch_is_locked_down_and_reads_its_password_from_its_secret() -> None:
    config = compose()
    service = config["services"]["torrent-watch"]
    assert service["read_only"] is True
    assert service["user"].startswith("${PUID")
    assert "ports" not in service
    assert "cap_add" not in service
    assert service["secrets"] == ["qbittorrent_password"]
    secret = config["secrets"]["qbittorrent_password"]["file"]
    assert secret == f"{CONFIG_ROOT}/torrent-watch/secrets/qbittorrent_password"
    assert config["configs"]["torrent_watch"]["file"] == "./scripts/torrent_watch.py"


def test_torrent_watch_limits_have_the_same_defaults_everywhere() -> None:
    environment = compose()["services"]["torrent-watch"]["environment"]
    example = dict(line.split("=", 1) for line in read(".env.example").splitlines() if line and line[0] != "#")
    config = torrent_watch.load_config({})
    limits = {
        "TORRENT_WATCH_MAX_TORRENT_GB": config.max_torrent,
        "TORRENT_WATCH_MAX_TOTAL_GB": config.max_total,
        "TORRENT_WATCH_MIN_FREE_GB": config.min_free,
    }
    for name, default in limits.items():
        assert environment[name] == f"${{{name}:-{default // torrent_watch.GB}}}"
        assert example[name] == str(default // torrent_watch.GB)
    # Out of the box it watches nothing, so the smoke test and a new installation add nothing.
    assert example["TORRENT_WATCH_TERMS"] == ""


def test_spoolman_reads_its_password_from_its_secret_and_its_server_only_from_spoolman_env() -> None:
    config = compose()
    spoolman = config["services"]["spoolman"]
    mount = "/run/secrets/spoolman_db_password"
    assert spoolman["environment"]["SPOOLMAN_DB_PASSWORD_FILE"] == mount
    assert "spoolman_db_password" in spoolman["secrets"]
    assert config["secrets"]["spoolman_db_password"]["file"] == f"{CONFIG_ROOT}/spoolman/secrets/db_password"
    # Spoolman refuses an empty database setting, so none may come from compose.yaml or .env.
    in_compose = {key for key in spoolman["environment"] if key.startswith("SPOOLMAN_DB_")}
    assert in_compose == {"SPOOLMAN_DB_PASSWORD_FILE"}
    example = dict(line.split("=", 1) for line in read("spoolman.env.example").splitlines() if line and line[0] != "#")
    assert all(example.values()), "spoolman.env.example leaves a setting empty"
    assert "SPOOLMAN_DB_PASSWORD" not in example
