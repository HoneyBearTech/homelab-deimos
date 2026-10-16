"""A stand-in for the `docker` CLI, enough of it for scripts/backup.sh, for tests/test_backup_stop.py.

The stack is in the JSON file $FAKE_DOCKER_STATE: its services, each `running`, `exited` or `dying` (a stop failed and
the container exits at `exits_at`, a time.time() value), plus the services whose stop fails (`fail_stop`: seconds
until the container exits after all) and that fail to start (`fail_start`). Every service has one data mount,
/config. `compose stop` and `compose start` are appended to `log` with the container's state at the time, so a test
can tell whether a start came before or after the container had exited.

A failed stop behaves as Docker does when a process can't exit (stuck on an NFS write): the command fails, and the
container keeps running until it exits on its own; `compose start` skips a container that is still running.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_DOCKER_STATE"])


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(stack: dict) -> None:
    STATE.write_text(json.dumps(stack), encoding="utf-8")


def state(stack: dict, service: str) -> str:
    """The container's state now: a dying container has exited once its time has come."""
    current = stack["services"][service]
    if current["state"] == "dying" and time.time() >= current["exits_at"]:
        return "exited"
    return current["state"]


def service_of(container: str) -> str:
    return container.removeprefix("id-")


def compose(stack: dict, args: list[str]) -> int:
    command, rest = args[0], [arg for arg in args[1:] if not arg.startswith("-")]
    if command == "config":
        print("\n".join(stack["services"]))
    elif command == "ps" and "--services" in args:
        # --status running: a dying container still runs.
        print("\n".join(s for s in stack["services"] if state(stack, s) in {"running", "dying"}))
    elif command == "ps":
        print(f"id-{rest[-1]}")
    elif command == "stop":
        return stop(stack, rest)
    elif command == "start":
        return start(stack, rest)
    else:
        print(f"fake docker: unexpected compose command {args}", file=sys.stderr)
        return 2
    return 0


def stop(stack: dict, services: list[str]) -> int:
    for service in services:
        stack["log"].append(f"stop {service} ({state(stack, service)})")
        if service in stack["fail_stop"]:
            stack["services"][service] = {"state": "dying", "exits_at": time.time() + stack["fail_stop"][service]}
            save(stack)
            print(
                f"Error response from daemon: cannot stop container: id-{service}: tried to kill container, "
                "but did not receive an exit event",
                file=sys.stderr,
            )
            return 1
        stack["services"][service] = {"state": "exited"}
    save(stack)
    return 0


def start(stack: dict, services: list[str]) -> int:
    failed = False
    for service in services:
        now = state(stack, service)
        stack["log"].append(f"start {service} ({now})")
        if now != "exited":
            continue  # Compose leaves a running container alone.
        if service in stack["fail_start"]:
            print(f"Error response from daemon: failed to start id-{service}", file=sys.stderr)
            failed = True
        else:
            stack["services"][service] = {"state": "running"}
    save(stack)
    return 1 if failed else 0


def inspect(stack: dict, args: list[str]) -> int:
    template, container = args[args.index("--format") + 1], args[-1]
    service = service_of(container)
    if "com.docker.compose.project" in template:
        print("fake")
    elif ".State.Running" in template:
        print("true" if state(stack, service) in {"running", "dying"} else "false")
    elif ".RW" in template:
        print(f"/config\t/srv/{service}/config")
    elif ".Mounts" in template:
        print("/config")
    elif ".Config.Image" in template:
        print(f"example/{service}:1")
    else:
        print()  # no labels, no networks
    return 0


def main(args: list[str]) -> int:
    stack = load()
    if args[0] == "compose":
        return compose(stack, args[1:])
    if args[0] == "inspect":
        return inspect(stack, args[1:])
    if args[:2] == ["network", "inspect"]:
        return 0
    if args[0] == "run":
        if "tar" in args:
            sys.stdout.write("an archive\n")
        return 0  # `test -d` on the mount
    print(f"fake docker: unexpected command {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
