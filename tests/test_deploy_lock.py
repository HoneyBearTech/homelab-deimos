"""Tests for deploy_lock in scripts/lib.sh, the lock backup.sh and restore.sh share with the pull agent.

A Python fcntl.flock on the same file stands in for the agent's `flock --nonblock` (both are flock(2) locks). The
tests that need the flock command are skipped where it isn't installed (macOS).
"""

import fcntl
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

LIB = Path(__file__).parent.parent / "scripts" / "lib.sh"
BASH = shutil.which("bash") or "/bin/bash"
STACK = "deimos"

needs_flock = pytest.mark.skipif(shutil.which("flock") is None, reason="needs flock (util-linux)")


def script(body: str) -> list[str]:
    """Bash code run with lib.sh sourced the way backup.sh and restore.sh source it."""
    return [BASH, "-c", f'set -euo pipefail; . "{LIB}"; {body}']


def environment(tmp_path: Path, where: str) -> tuple[dict[str, str], Path]:
    """An environment naming the agent's state root through `where`, and the stack's state directory under it."""
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")}
    if where == "HOMELAB_PULL_STATE_DIR":
        env[where] = str(tmp_path / "state")
        return env, tmp_path / "state" / STACK
    env[where] = str(tmp_path / "xdg")
    return env, tmp_path / "xdg" / "homelab-pull" / STACK


def run(body: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # Fixed bash code from this file.
    return subprocess.run(script(body), capture_output=True, text=True, env=env, check=False, timeout=60)  # noqa: S603


def lock_is_free(lock: Path) -> bool:
    with lock.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


@contextmanager
def deploy(lock: Path) -> Iterator[None]:
    """The agent in the middle of a deploy: holds the stack's lock."""
    with lock.open("a") as file:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


WHERE = pytest.mark.parametrize("where", ["HOMELAB_PULL_STATE_DIR", "XDG_STATE_HOME"])


@WHERE
def test_does_nothing_without_the_agents_state(tmp_path: Path, where: str) -> None:
    env, state = environment(tmp_path, where)
    result = run("deploy_lock; echo after", env)
    assert result.returncode == 0
    assert result.stdout == "after\n"
    assert result.stderr == ""
    assert not state.parent.exists()


def test_fails_when_flock_is_missing(tmp_path: Path) -> None:
    env, state = environment(tmp_path, "HOMELAB_PULL_STATE_DIR")
    state.mkdir(parents=True)
    empty = tmp_path / "empty"
    empty.mkdir()
    # Only builtins run before the check, so an empty PATH hides flock without breaking the script.
    result = run("deploy_lock; echo after", {**env, "PATH": str(empty)})
    assert result.returncode != 0
    assert "after" not in result.stdout
    assert "flock isn't installed" in result.stderr


@needs_flock
@WHERE
def test_holds_the_lock_until_the_script_exits(tmp_path: Path, where: str) -> None:
    env, state = environment(tmp_path, where)
    state.mkdir(parents=True)
    lock = state / "lock"
    marker = tmp_path / "locked"
    # exec: the one process left holding the lock is the one killed below.
    holder = subprocess.Popen(script(f'deploy_lock; touch "{marker}"; exec sleep 30'), env=env)  # noqa: S603
    try:
        deadline = time.monotonic() + 20
        while not marker.exists():
            assert time.monotonic() < deadline, "deploy_lock didn't take the lock"
            time.sleep(0.05)
        assert not lock_is_free(lock)
    finally:
        holder.kill()
        holder.wait()
    assert lock_is_free(lock)


@needs_flock
def test_waits_for_a_running_deploy(tmp_path: Path) -> None:
    env, state = environment(tmp_path, "HOMELAB_PULL_STATE_DIR")
    state.mkdir(parents=True)
    with deploy(state / "lock"):
        waiting = subprocess.Popen(  # noqa: S603 # fixed bash code from this file
            script("deploy_lock; echo after"), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert waiting.stdout is not None
        line = waiting.stdout.readline()
        assert line == "Waiting for a deploy by the pull agent to finish (up to 1800 s)\n"
        assert waiting.poll() is None
    out, err = waiting.communicate(timeout=30)
    assert waiting.returncode == 0
    assert out == "after\n"
    assert err == ""


@needs_flock
def test_times_out_and_runs_nothing_after(tmp_path: Path) -> None:
    env, state = environment(tmp_path, "HOMELAB_PULL_STATE_DIR")
    state.mkdir(parents=True)
    with deploy(state / "lock"):
        result = run("deploy_lock_timeout=1; deploy_lock; echo after", env)
    assert result.returncode != 0
    assert result.stdout == "Waiting for a deploy by the pull agent to finish (up to 1 s)\n"
    assert f"the pull agent's deploy of {STACK} still holds {state}/lock after 1 s;" in result.stderr
    assert f"nothing was stopped (check: journalctl --user -u homelab-pull@{STACK})" in result.stderr
