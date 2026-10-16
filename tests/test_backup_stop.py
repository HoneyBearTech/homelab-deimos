"""scripts/backup.sh when stopping a service fails: no service is left down.

Each test runs backup.sh against tests/fake_docker.py, a stand-in for the docker CLI, with a stack of three services:
`app` (the one whose stop may fail), `web` (running) and `idle` (stopped before the backup, so it stays stopped).
A failed stop is what Docker does when a process can't exit in time, stuck writing to an NFS share: the stop fails
and the container exits some seconds later. Before the fix, backup.sh started the services again at once (Compose
skipped the dying container) and `app` stayed down.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BACKUP = ROOT / "scripts" / "backup.sh"
FAKE = Path(__file__).parent / "fake_docker.py"
BASH = shutil.which("bash") or "/bin/bash"


class Stack:
    """The fake stack's state file, and backup.sh run against it."""

    def __init__(self, tmp_path: Path, fail_stop: dict[str, float] | None = None, fail_start: tuple = ()) -> None:
        self.tmp = tmp_path
        self.file = tmp_path / "state.json"
        self.file.write_text(
            json.dumps(
                {
                    "services": {"app": {"state": "running"}, "web": {"state": "running"}, "idle": {"state": "exited"}},
                    "fail_stop": fail_stop or {},
                    "fail_start": list(fail_start),
                    "log": [],
                }
            ),
            encoding="utf-8",
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        docker.chmod(0o755)
        (tmp_path / "empty.env").touch()
        self.env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": "/nonexistent",
            "FAKE_DOCKER_STATE": str(self.file),
            # Never a real pull agent's lock, never the checkout's real settings.
            "HOMELAB_PULL_STATE_DIR": str(tmp_path / "homelab-pull"),
            "COMPOSE_ENV_FILES": str(tmp_path / "empty.env"),
        }

    def backup(self, stop_wait: int) -> subprocess.CompletedProcess[str]:
        # S603: fixed argv, no shell.
        return subprocess.run(  # noqa: S603
            [BASH, str(BACKUP), str(self.tmp / "backup")],
            env={**self.env, "BACKUP_STOP_WAIT": str(stop_wait)},
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def states(self) -> dict[str, str]:
        """Each service's state now: a dying container whose time has come has exited (as in fake_docker.py)."""
        now = time.time()
        return {
            service: "exited" if container["state"] == "dying" and now >= container["exits_at"] else container["state"]
            for service, container in self.load()["services"].items()
        }

    def load(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8"))

    def log(self) -> list[str]:
        return self.load()["log"]


def test_a_normal_backup_starts_again_what_was_running(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    out = stack.backup(stop_wait=5)
    assert out.returncode == 0, out.stderr
    assert stack.states() == {"app": "running", "web": "running", "idle": "exited"}
    assert "stop app (running)" in stack.log()
    assert "start app (exited)" in stack.log()
    # A service that wasn't running isn't stopped for its backup, nor started after it.
    assert not [line for line in stack.log() if "idle" in line]
    assert (tmp_path / "backup" / "app--config.tar.gz").is_file()
    assert (tmp_path / "backup" / "idle--config.tar.gz").is_file()


def test_a_container_that_exits_after_its_stop_failed_is_backed_up_and_started_again(tmp_path: Path) -> None:
    stack = Stack(tmp_path, fail_stop={"app": 2})
    out = stack.backup(stop_wait=30)
    assert out.returncode == 0, out.stderr
    assert "warning: stopping app failed; waiting up to 30s for its container to exit" in out.stderr
    assert "warning: app exited after its stop failed" in out.stderr
    assert stack.states() == {"app": "running", "web": "running", "idle": "exited"}
    # Started once it had exited, not while it was still on its way down.
    assert "start app (exited)" in stack.log()
    assert "start app (dying)" not in stack.log()
    # The rest of the backup ran.
    for service in ("app", "web", "idle"):
        assert (tmp_path / "backup" / f"{service}--config.tar.gz").is_file()
    assert (tmp_path / "backup" / "SHA256SUMS").is_file()


def test_a_container_that_exits_later_still_fails_the_backup_but_is_started_again(tmp_path: Path) -> None:
    # Still running when the first wait ends (after 5 to 6 s: the shell counts whole seconds), gone during the second.
    stack = Stack(tmp_path, fail_stop={"app": 8})
    out = stack.backup(stop_wait=6)
    assert out.returncode == 1
    assert "error: app still runs 6s after its stop failed; the backup stops here" in out.stderr
    assert "Waiting up to 6s more for app to exit before starting the services again" in out.stdout
    assert stack.states() == {"app": "running", "web": "running", "idle": "exited"}
    assert "start app (exited)" in stack.log()
    assert not (tmp_path / "backup" / "SHA256SUMS").exists()


def test_a_container_that_never_exits_fails_the_backup_loudly(tmp_path: Path) -> None:
    stack = Stack(tmp_path, fail_stop={"app": 3600})
    out = stack.backup(stop_wait=1)
    assert out.returncode == 1
    assert "error: app never exited after its stop failed; it may still go down" in out.stderr
    assert "docker compose start app" in out.stderr
    assert stack.states() == {"app": "dying", "web": "running", "idle": "exited"}


def test_a_service_that_doesnt_start_again_fails_the_backup_and_is_named(tmp_path: Path) -> None:
    stack = Stack(tmp_path, fail_start=("app",))
    out = stack.backup(stop_wait=5)
    assert out.returncode == 1
    assert "error: not running again after the backup: app (start them: docker compose start app)" in out.stderr
    assert stack.states()["web"] == "running"


@pytest.mark.parametrize("value", ["soon", "-1", "1.5"])
def test_a_bad_stop_wait_fails_before_anything_stops(tmp_path: Path, value: str) -> None:
    stack = Stack(tmp_path)
    out = subprocess.run(  # noqa: S603 # fixed argv, no shell
        [BASH, str(BACKUP), str(tmp_path / "backup")],
        env={**stack.env, "BACKUP_STOP_WAIT": value},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert out.returncode == 1
    assert f"BACKUP_STOP_WAIT must be a number of seconds, not '{value}'" in out.stderr
    assert stack.log() == []
