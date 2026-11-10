from __future__ import annotations

import email
import email.policy
import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest
import torrent_watch as tw

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

GB = tw.GB
OLD = "1" * 40
NEW = "2" * 40
NEXT = "3" * 40
REV = "a" * 40
KEY = "c" * 40


def bencode(value: object) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    assert isinstance(value, dict)
    return b"d" + b"".join(bencode(key) + bencode(item) for key, item in sorted(value.items())) + b"e"


def make_torrent(label: str) -> bytes:
    """A small .torrent file of its own for each label, with a web seed and a field after the info dictionary."""
    info = {b"name": label.encode(), b"piece length": 16384, b"length": 1, b"pieces": bytes.fromhex(label[:40])}
    return bencode({b"announce": b"udp://t", b"info": info, b"url-list": b"https://i/"})


def row(repo: str, infohash: str, seeds: int = 1) -> str:
    return f"{repo:<40}  1 GB  {seeds} seeds  magnet:?xt=urn:btih:{infohash}&tr=udp%3A%2F%2Ftracker%3A1"


class Site:
    """A stand-in for the index and qBittorrent, on one local HTTP server."""

    def __init__(self) -> None:
        self.search: dict[str, list[str]] = {}
        self.models: dict[str, dict] = {}
        self.torrents: dict[str, dict] = {}
        self.free = 2000 * GB
        self.password = "right"  # noqa: S105 # a stand-in's
        self.requests: list[str] = []
        self.added: list[dict[str, str]] = []
        self.stopped: list[str] = []
        self.forced: list[str] = []
        self.force_status = HTTPStatus.OK
        self.catalogue: dict[str, list[dict]] = {}
        self.torrent_answers: dict[str, dict] = {}
        self.torrent_files: dict[str, bytes] = {}
        self.real: dict[str, str] = {}
        self.labels: dict[str, str] = {}
        self.removed: list[dict[str, str]] = []
        self.remove_status = HTTPStatus.OK
        self.source_files: dict[str, bytes] = {}
        self.source_status = HTTPStatus.OK
        self.ranges: list[str] = []
        self.honour_range = True
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.created = make_torrent("4" * 40)
        self.creation_status = "Finished"
        self.created_seeding = True
        self.file_lists: dict[str, list[dict]] = {}
        self.catalogue_answer: str | None = None
        self.index_status = HTTPStatus.OK
        self.add_answer = '{"failure_count":0,"success_count":1}'
        self.refusal = HTTPStatus.UNAUTHORIZED
        self.logins = 0
        self.session_ends: set[str] = set()
        self.always_forbidden: set[str] = set()

    def catalogue_page(self, query: dict[str, list[str]]) -> tuple[HTTPStatus, str, dict[str, str]]:
        if self.catalogue_answer is not None:
            return self.index_status, self.catalogue_answer, {}
        models = self.catalogue.get(query["license"][0], [])
        start = int(query.get("cursor", ["0"])[0])
        next_start = start + 2
        cursor = str(next_start) if next_start < len(models) else None
        return self.index_status, json.dumps({"models": models[start:next_start], "nextCursor": cursor}), {}

    def list_at_risk(self, repo: str, infohash: str, gigabytes: int = 1, **fields: object) -> None:
        license_name = str(fields.pop("license", "mit"))
        model = {
            "id": repo,
            "license": license_name,
            "sizeBytes": gigabytes * GB,
            "seeded": True,
            "seed": 0,
            "hfBacked": True,
            **fields,
        }
        self.catalogue.setdefault(license_name, []).append(model)
        self.serve_torrent(repo, infohash)

    def source_answer(self, path: str, form: dict[str, str]) -> tuple[HTTPStatus, bytes | str, dict[str, str]]:
        data = self.source_files.get(urllib.parse.unquote(path))
        if self.source_status != HTTPStatus.OK or data is None:
            return (self.source_status if data is not None else HTTPStatus.NOT_FOUND), "no", {}
        start = int(form["range"].removeprefix("bytes=").rstrip("-")) if form.get("range") else 0
        if form.get("range"):
            self.ranges.append(form["range"])
        if not form.get("range") or not self.honour_range:
            return HTTPStatus.OK, data, {}
        if start >= len(data):
            return HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "", {}
        return HTTPStatus.PARTIAL_CONTENT, data[start:], {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"}

    def qbittorrent_answer(
        self, url: urllib.parse.SplitResult, form: dict[str, str]
    ) -> tuple[HTTPStatus, bytes | str, dict[str, str]]:
        name = url.path.removeprefix("/api/v2/")
        self.calls.append((name, form))
        query = dict(urllib.parse.parse_qsl(url.query))
        if name == "torrentcreator/addTask":
            if self.created_seeding:
                self.torrents[KEY] = {"infohash_v1": tw.info_hash(self.created), "category": "", "total_size": 9}
            return HTTPStatus.OK, '{"taskID":"task-1"}', {}
        if name == "torrentcreator/status":
            status = [{"taskID": query["taskID"], "status": self.creation_status, "errorMessage": "disk full"}]
            return HTTPStatus.OK, json.dumps(status if self.creation_status else []), {}
        if name == "torrentcreator/torrentFile":
            return HTTPStatus.OK, self.created, {}
        if name == "torrents/info":
            keys = query["hashes"].split("|")
            found = [
                {"hash": key, "state": self.torrents[key].get("state", "")} for key in keys if key in self.torrents
            ]
            return HTTPStatus.OK, json.dumps(found), {}
        if name == "torrents/files":
            return HTTPStatus.OK, json.dumps(self.file_lists.get(query["hash"], [])), {}
        return HTTPStatus.OK, "", {}

    def needs_torrent(self, url: str, repo: str = "a/bare", files: dict[str, bytes] | None = None) -> dict[str, bytes]:
        """List repo at risk with no .torrent file, and serve its files at its source."""
        files = files if files is not None else {"model.bin": b"weights" * 1000, "sub/config.json": b"{}"}
        self.list_at_risk(repo, NEW)
        self.serve_torrent(repo, NEW, file=False)
        self.source_listing(url, repo, files)
        return files

    def source_listing(self, url: str, repo: str, files: dict[str, bytes]) -> None:
        answer = self.torrent_answers[repo]
        seed = urllib.parse.quote(f"{url}/api/ws/{repo}/{REV}/", safe="")
        answer["magnet"] += f"&tr=udp%3A%2F%2Ftracker.example%3A6969%2Fannounce&tr=ftp%3A%2F%2Fbad&ws={seed}"
        answer["revision"] = REV
        answer["files"] = [{"path": path, "sha256": hashlib.sha256(data).hexdigest()} for path, data in files.items()]
        for path, data in files.items():
            self.source_files[f"/{repo}/resolve/{REV}/{path}"] = data

    def serve_torrent(self, repo: str, label: str, *, file: bool = True) -> None:
        """List a torrent for repo, known in the tests by label, with its .torrent file unless file is False."""
        torrent = make_torrent(label)
        real = tw.info_hash(torrent)
        self.real[label], self.labels[real] = real, label
        self.torrent_answers[repo] = {"magnet": f"magnet:?xt=urn:btih:{real}&ws=https%3A%2F%2Fi", "seeders": 0}
        if file:
            self.torrent_files[real] = torrent
            self.torrent_answers[repo]["torrent"] = f"/api/torrents/{real}.torrent"

    def answer(  # noqa: C901, PLR0911, PLR0912 # one per route
        self, path: str, form: dict[str, str]
    ) -> tuple[HTTPStatus, str | bytes, dict[str, str]]:
        url = urllib.parse.urlsplit(path)
        self.requests.append(path)
        name = url.path.removeprefix("/api/v2/")
        if name in self.session_ends or name in self.always_forbidden:
            self.session_ends.discard(name)
            return HTTPStatus.FORBIDDEN, "Forbidden", {}
        if url.path.startswith("/s/"):
            term = urllib.parse.unquote_plus(url.path.removeprefix("/s/"))
            lines = self.search.get(term)
            text = "\n".join(lines) + "\n" if lines else f'{tw.NO_MATCH} "{term}" yet.\n'
            return self.index_status, text, {}
        if url.path == "/api/models" and "license=" in url.query:
            return self.catalogue_page(urllib.parse.parse_qs(url.query))
        if "/resolve/" in url.path:
            return self.source_answer(url.path, form)
        prefixes = ("/api/v2/torrentcreator/", "/api/v2/torrents/files", "/api/v2/torrents/re", "/api/v2/torrents/info")
        if url.path.startswith(prefixes):
            return self.qbittorrent_answer(url, form)
        if url.path in {"/api/v2/torrents/setCategory", "/api/v2/torrents/start"}:
            self.calls.append((url.path.removeprefix("/api/v2/"), form))
            return HTTPStatus.OK, "", {}
        if url.path.startswith("/api/torrents/") and url.path.endswith(".torrent"):
            torrent = self.torrent_files.get(url.path.removeprefix("/api/torrents/").removesuffix(".torrent"))
            return (self.index_status, torrent, {}) if torrent else (HTTPStatus.NOT_FOUND, "not here", {})
        if url.path == "/api/v2/torrents/delete":
            self.removed.append(form)
            return self.remove_status, "", {}
        if url.path == "/api/torrents":
            repo = urllib.parse.parse_qs(url.query)["repo"][0]
            return self.index_status, json.dumps(self.torrent_answers.get(repo, {"magnet": None})), {}
        if url.path == "/api/models":
            repo = urllib.parse.parse_qs(url.query)["q"][0]
            models = [{"id": repo, **self.models[repo]}] if repo in self.models else []
            return self.index_status, json.dumps({"models": models}), {}
        if url.path == "/api/v2/auth/login":
            self.logins += 1
            if form.get("password") == self.password:
                return HTTPStatus.OK, "Ok.", {"Set-Cookie": "SID=session; path=/"}
            return self.refusal, "Fails.", {}
        if url.path == "/api/v2/sync/maindata":
            data = {"torrents": self.torrents, "server_state": {"free_space_on_disk": self.free}}
            return HTTPStatus.OK, json.dumps(data), {}
        if url.path == "/api/v2/torrents/add":
            if "torrents" in form:
                form["infohash"] = tw.info_hash(bytes.fromhex(form["torrents"]))
            self.added.append(form)
            return HTTPStatus.OK, self.add_answer, {}
        if url.path == "/api/v2/torrents/stop":
            self.stopped.extend(form["hashes"].split("|"))
            return HTTPStatus.OK, "", {}
        if url.path == "/api/v2/torrents/setForceStart":
            if self.force_status == HTTPStatus.OK and form["value"] == "true":
                self.forced.extend(form["hashes"].split("|"))
            return self.force_status, "", {}
        return HTTPStatus.NOT_FOUND, "not here", {}


@pytest.fixture
def site() -> Iterator[tuple[Site, str]]:
    stand_in = Site()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, form: dict[str, str]) -> None:
            status, text, headers = stand_in.answer(self.path, form)
            body = text if isinstance(text, bytes) else text.encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.respond({"range": self.headers["Range"]} if self.headers.get("Range") else {})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("multipart/form-data"):
                self.respond(dict(urllib.parse.parse_qsl(raw.decode())))
                return
            head = f"Content-Type: {content_type}\r\n\r\n".encode()
            message = email.message_from_bytes(head + raw, policy=email.policy.HTTP)
            form = {}
            for part in message.iter_parts():
                payload = part.get_payload(decode=True)
                name = part.get_param("name", header="content-disposition")
                form[name] = payload.hex() if part.get_filename() else payload.decode()
            self.respond(form)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 # as the base class names it
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield stand_in, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


def make_config(url: str, tmp_path: Path, *terms: str, **limits: int) -> tw.Config:
    password = tmp_path / "password"
    if not password.exists():
        password.write_text("right\n", encoding="utf-8")
    return tw.Config(
        terms=tuple(tw.parse_term(term) for term in terms),
        index_url=url,
        qbittorrent_url=url,
        username="admin",
        password_file=password,
        state_file=tmp_path / "state.json",
        max_torrent=limits.get("max_torrent", 100) * GB,
        max_total=limits.get("max_total", 500) * GB,
        min_free=limits.get("min_free", 500) * GB,
        pause_seconds=0,
        source_url=url,
        files_dir=tmp_path / "files",
        work_seconds=60,
    )


def run(config: tw.Config) -> None:
    client = tw.Qbittorrent(config.qbittorrent_url, config.username, config.password_file)
    tw.Watch(config, tw.Index(config.index_url), client, tw.load_state(config.state_file)).run()


def state_of(config: tw.Config) -> dict:
    return json.loads(config.state_file.read_text(encoding="utf-8"))


def magnets(stand_in: Site) -> list[str]:
    """What was added, by label: from the .torrent file's infohash, or the magnet link's."""
    found = [form.get("infohash") or form["urls"].split("&")[0].rsplit(":", 1)[1] for form in stand_in.added]
    return [stand_in.labels.get(infohash, infohash) for infohash in found]


@pytest.fixture
def watching(site: tuple[Site, str], tmp_path: Path) -> tuple[Site, tw.Config]:
    """A term that has run once, when it matched the torrent OLD."""
    stand_in, url = site
    stand_in.search["qwen"] = [row("a/old", OLD)]
    config = make_config(url, tmp_path, "qwen")
    run(config)
    return stand_in, config


def test_first_run_of_a_term_records_what_it_matches_and_adds_nothing(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    assert stand_in.added == []
    state = state_of(config)
    assert state["terms"] == ["qwen"]
    assert state["done"][OLD]["why"] == "listed before"
    assert not any(path.startswith("/api/models") for path in stand_in.requests)


def test_a_new_torrent_is_added_under_its_category_and_folder_once(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "Apache-2.0", "sizeBytes": 10 * GB}
    run(config)
    assert magnets(stand_in) == [NEW]
    assert stand_in.added[0]["category"] == tw.CATEGORY
    assert stand_in.added[0]["savepath"] == tw.SAVE_PATH
    assert stand_in.added[0]["autoTMM"] == "false"
    # Removed from qBittorrent later, it stays out.
    run(config)
    assert magnets(stand_in) == [NEW]


def test_a_licence_outside_the_list_is_refused_logged_once_and_not_looked_up_again(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "other", "sizeBytes": GB}
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
        run(config)
    assert stand_in.added == []
    assert sum("its licence is other" in message for message in caplog.messages) == 1
    assert sum(path.startswith("/api/models") for path in stand_in.requests) == 1


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ({}, "unknown"),
        ({"license": None, "sizeBytes": GB}, "unknown"),
        ({"license": "mit"}, "size is unknown"),
        ({"license": "mit", "sizeBytes": 0}, "size is unknown"),
        ({"license": "mit", "sizeBytes": "1"}, "size is unknown"),
        ({"license": "mit", "sizeBytes": True}, "size is unknown"),
        ({"license": "mit", "sizeBytes": 101 * GB}, "limit per torrent"),
    ],
)
def test_what_it_cant_tell_or_too_large_counts_as_no(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture, model: dict, expected: str
) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = model
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
    assert stand_in.added == []
    assert any(expected in message for message in caplog.messages)


def test_a_torrent_refused_for_now_is_added_when_it_can_be(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit"}
    run(config)
    assert stand_in.added == []
    stand_in.models["a/new"]["sizeBytes"] = GB
    run(config)
    assert magnets(stand_in) == [NEW]
    assert NEW not in state_of(config)["pending"]


def test_the_limit_for_all_of_them_counts_what_the_category_holds(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit", "sizeBytes": 60 * GB}
    held = {"category": tw.CATEGORY, "total_size": 450 * GB, "amount_left": 0, "state": "stalledUP"}
    other = {"category": "", "total_size": 900 * GB, "amount_left": 0, "state": "stalledUP"}
    stand_in.torrents = {"9" * 40: held, "8" * 40: other}
    run(config)
    assert stand_in.added == []
    # Removing one from qBittorrent makes room again.
    del stand_in.torrents["9" * 40]
    run(config)
    assert magnets(stand_in) == [NEW]


def test_free_space_counts_everything_still_downloading_and_the_new_torrent(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit", "sizeBytes": 60 * GB}
    stand_in.free = 1000 * GB
    stand_in.torrents = {"9" * 40: {"category": "", "total_size": 450 * GB, "amount_left": 450 * GB, "state": "dl"}}
    run(config)
    assert stand_in.added == []
    stand_in.torrents["9" * 40]["amount_left"] = 400 * GB
    run(config)
    assert magnets(stand_in) == [NEW]


def test_a_torrent_with_no_metadata_yet_counts_at_its_listed_size(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"] += [row("a/new", NEW), row("a/next", NEXT)]
    stand_in.models["a/new"] = {"license": "mit", "sizeBytes": 90 * GB}
    run(config)
    assert magnets(stand_in) == [NEW]
    # While the first one has no metadata, qBittorrent reports its size as -1 or 0.
    stand_in.torrents = {NEW: {"category": tw.CATEGORY, "total_size": -1, "amount_left": 0, "state": "metaDL"}}
    stand_in.models["a/next"] = {"license": "mit", "sizeBytes": 90 * GB}
    config = config._replace(max_total=170 * GB)
    run(config)
    assert magnets(stand_in) == [NEW]


def test_it_adds_at_most_a_few_per_poll(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    new = [f"{number:040x}" for number in range(100, 105)]
    for infohash in new:
        stand_in.search["qwen"].append(row(f"a/{infohash[-3:]}", infohash))
        stand_in.models[f"a/{infohash[-3:]}"] = {"license": "mit", "sizeBytes": GB}
    run(config)
    assert magnets(stand_in) == new[: tw.MAX_ADDS_PER_POLL]
    run(config)
    assert magnets(stand_in) == new


def test_a_torrent_already_in_qbittorrent_is_left_alone(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    # A hybrid torrent's key in qBittorrent isn't the hash its magnet link names.
    stand_in.torrents = {"f" * 40: {"infohash_v1": NEW, "category": "", "total_size": GB, "amount_left": 0}}
    run(config)
    assert stand_in.added == []
    assert state_of(config)["done"][NEW]["why"] == "already in qBittorrent"
    assert not any(path.startswith("/api/models") for path in stand_in.requests)


def test_below_the_free_space_it_stops_only_its_own_unfinished_downloads(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.free = 100 * GB
    downloading = {"category": tw.CATEGORY, "total_size": GB, "amount_left": GB, "state": "downloading"}
    stand_in.torrents = {
        "a" * 40: downloading,
        "b" * 40: {**downloading, "amount_left": 0, "state": "uploading"},
        "c" * 40: {**downloading, "state": "stoppedDL"},
        "d" * 40: {**downloading, "category": ""},
    }
    run(config)
    assert stand_in.stopped == ["a" * 40]


def test_with_space_it_stops_nothing(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    downloading = {"category": tw.CATEGORY, "total_size": GB, "amount_left": GB, "state": "downloading"}
    stand_in.torrents = {"a" * 40: downloading}
    run(config)
    assert stand_in.stopped == []


def a_torrent(**fields: object) -> dict[str, object]:
    return {"category": tw.CATEGORY, "total_size": GB, "amount_left": 0, "state": "stalledUP", **fields}


@pytest.mark.parametrize("state", sorted(tw.SEEDING_STATES))
def test_a_finished_torrent_is_switched_to_forced_seeding(watching: tuple[Site, tw.Config], state: str) -> None:
    stand_in, config = watching
    stand_in.torrents = {"a" * 40: a_torrent(state=state)}
    run(config)
    assert stand_in.forced == ["a" * 40]


@pytest.mark.parametrize("category", ["", "other", tw.CATEGORY])
def test_forced_seeding_covers_every_torrent_whatever_its_category(
    watching: tuple[Site, tw.Config], category: str
) -> None:
    stand_in, config = watching
    stand_in.torrents = {"a" * 40: a_torrent(category=category)}
    run(config)
    assert stand_in.forced == ["a" * 40]


def test_low_space_still_stops_only_its_own_downloads_though_forcing_covers_every_torrent(
    watching: tuple[Site, tw.Config],
) -> None:
    stand_in, config = watching
    stand_in.free = 100 * GB
    stand_in.torrents = {
        "a" * 40: a_torrent(category="", state="downloading", amount_left=GB),
        "b" * 40: a_torrent(category=""),
    }
    run(config)
    assert stand_in.stopped == []
    assert stand_in.forced == ["b" * 40]


def test_forced_seeding_happens_once_so_a_torrent_switched_back_by_hand_stays_so(
    watching: tuple[Site, tw.Config],
) -> None:
    stand_in, config = watching
    stand_in.torrents = {"a" * 40: a_torrent()}
    run(config)
    run(config)
    assert stand_in.forced == ["a" * 40]
    assert state_of(config)["forced"] == ["a" * 40]
    # One that finishes later is still switched.
    stand_in.torrents["b" * 40] = a_torrent(state="uploading")
    run(config)
    assert stand_in.forced == ["a" * 40, "b" * 40]


@pytest.mark.parametrize(
    "torrent",
    [
        # A magnet link with no metadata yet: nothing left to download, but nothing to seed either.
        a_torrent(state="metaDL", total_size=-1),
        a_torrent(state="downloading", amount_left=GB),
        a_torrent(state="stoppedUP"),
        a_torrent(state="forcedUP"),
        a_torrent(state="error"),
    ],
)
def test_forced_seeding_leaves_everything_else_alone(watching: tuple[Site, tw.Config], torrent: dict) -> None:
    stand_in, config = watching
    stand_in.torrents = {"a" * 40: torrent}
    run(config)
    assert stand_in.forced == []


def test_failing_to_force_seeding_doesnt_hold_up_the_poll_and_is_tried_again(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    stand_in.torrents = {"a" * 40: a_torrent()}
    stand_in.force_status = HTTPStatus.BAD_GATEWAY
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit", "sizeBytes": GB}
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert magnets(stand_in) == [NEW]
    assert any("forced seeding yet" in message for message in caplog.messages)
    assert state_of(config)["forced"] == []
    stand_in.force_status = HTTPStatus.OK
    run(config)
    assert stand_in.forced == ["a" * 40]


@pytest.fixture
def at_risk(site: tuple[Site, str], tmp_path: Path) -> tuple[Site, tw.Config]:
    """A config that watches what is at risk, and no terms."""
    stand_in, url = site
    return stand_in, make_config(url, tmp_path)._replace(at_risk=True)


def scans(stand_in: Site) -> int:
    return sum(path.startswith("/api/models?license=") and "cursor" not in path for path in stand_in.requests)


def test_at_risk_is_off_unless_asked_for(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.list_at_risk("a/risky", NEW)
    run(config)
    assert stand_in.added == []
    assert scans(stand_in) == 0


def test_at_risk_adds_what_is_there_already_smallest_first_under_its_category(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/large", OLD, 9)
    stand_in.list_at_risk("a/small", NEW, 1, license="apache-2.0")
    stand_in.list_at_risk("a/medium", NEXT, 5)
    run(config)
    assert magnets(stand_in) == [NEW, NEXT, OLD]
    assert {form["category"] for form in stand_in.added} == {tw.CATEGORY}
    assert state_of(config)["done"][stand_in.real[NEW]] == {"repo": "a/small", "why": "added", "size": GB}
    assert all("torrents" in form and "urls" not in form for form in stand_in.added)


def test_at_risk_follows_the_list_across_its_pages_for_every_allowed_licence(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    for number in range(5):
        stand_in.list_at_risk(f"a/mit{number}", f"{number + 10:040x}")
    stand_in.list_at_risk("a/open", f"{50:040x}", license="openmdw-1.1")
    stand_in.list_at_risk("a/free", f"{60:040x}", license="wtfpl")
    run(config)
    models = state_of(config)["at_risk"]["models"]
    assert {model["repo"] for model in models} == {f"a/mit{number}" for number in range(5)} | {"a/open", "a/free"}
    assert sum("license=mit" in path for path in stand_in.requests) == 3
    assert scans(stand_in) == len(tw.ALLOWED_LICENSES)


def test_at_risk_stops_paging_a_list_that_never_ends(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.catalogue_answer = json.dumps({"models": [], "nextCursor": "again"})
    run(config)
    pages = sum(path.startswith("/api/models?license=") for path in stand_in.requests)
    assert pages == tw.MAX_PAGES_PER_LICENSE * len(tw.ALLOWED_LICENSES)


@pytest.mark.parametrize(
    "fields",
    [
        {"seed": 1},
        {"seed": None},
        {"seed": False},
        {"seeded": False},
        {"hfBacked": False},
        {"hfBacked": None},
    ],
)
def test_at_risk_means_a_torrent_no_seeders_and_its_source_still_there(
    at_risk: tuple[Site, tw.Config], fields: dict
) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/fine", NEW, 1, **fields)
    run(config)
    assert stand_in.added == []
    assert state_of(config)["at_risk"]["models"] == []


def test_at_risk_leaves_out_a_licence_the_filter_let_through(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/other", NEW)
    stand_in.catalogue["mit"][0]["license"] = "other"
    run(config)
    assert stand_in.added == []


def test_at_risk_keeps_to_the_limits_and_says_so(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = at_risk
    config = config._replace(max_torrent=10 * GB, max_total=6 * GB)
    stand_in.list_at_risk("a/fits", NEW, 5)
    stand_in.list_at_risk("a/over-total", NEXT, 7)
    stand_in.list_at_risk("a/over-each", OLD, 11)
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
    assert magnets(stand_in) == [NEW]
    assert any("3 listed, 2 kept out by the limits, 0 waiting" in message for message in caplog.messages)


def test_at_risk_counts_the_free_space_of_what_it_just_added(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.free = 510 * GB
    stand_in.list_at_risk("a/first", NEW, 8)
    stand_in.list_at_risk("a/second", NEXT, 8)
    run(config)
    assert magnets(stand_in) == [NEW]


def test_at_risk_adds_a_few_per_poll_from_the_list_it_has_and_asks_again_when_it_is_old(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = at_risk
    new = [f"{number:040x}" for number in range(100, 105)]
    for number, infohash in enumerate(new):
        stand_in.list_at_risk(f"a/{number}", infohash, number + 1)
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
    assert magnets(stand_in) == new[: tw.MAX_ADDS_PER_POLL]
    assert any("5 listed, 0 kept out by the limits, 2 waiting" in message for message in caplog.messages)
    asked = scans(stand_in)
    run(config)
    assert magnets(stand_in) == new
    assert scans(stand_in) == asked
    state = state_of(config)
    state["at_risk"]["scanned"] -= tw.AT_RISK_RESCAN_SECONDS
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    assert scans(stand_in) == 2 * asked


def test_a_torrent_removed_from_qbittorrent_isnt_added_again_by_at_risk(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    run(config)
    state = state_of(config)
    state["at_risk"]["scanned"] = 0
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    assert magnets(stand_in) == [NEW]


def test_at_risk_takes_what_a_term_only_recorded_at_its_first_run(site: tuple[Site, str], tmp_path: Path) -> None:
    stand_in, url = site
    stand_in.search["qwen"] = [row("a/risky", NEW)]
    config = make_config(url, tmp_path, "qwen")
    run(config)
    assert stand_in.added == []
    stand_in.list_at_risk("a/risky", NEW)
    run(config._replace(at_risk=True))
    assert magnets(stand_in) == [NEW]


def test_a_model_already_in_qbittorrent_is_left_alone_by_at_risk(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/mine", NEW)
    real = stand_in.real[NEW]
    stand_in.torrents = {"f" * 40: {"infohash_v1": real, "category": "", "total_size": GB, "amount_left": 0}}
    run(config)
    assert stand_in.added == []
    assert state_of(config)["done"][real]["why"] == "already in qBittorrent"


@pytest.mark.parametrize(
    "answer",
    [
        {"magnet": None, "terms": {"id": "x"}, "seeders": 0},
        {"magnet": f"magnet:?xt=urn:btih:{NEW}", "terms": {"id": "x"}, "seeders": 0},
        {"magnet": f"magnet:?xt=urn:btih:{NEW}", "seeders": 2},
        {"magnet": f"magnet:?xt=urn:btih:{NEW}"},
        {"magnet": "https://elsewhere/file.torrent", "seeders": 0},
        {"magnet": 1, "seeders": 0},
    ],
)
def test_at_risk_drops_a_model_whose_torrent_isnt_as_listed_any_more(
    at_risk: tuple[Site, tw.Config], answer: dict
) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    stand_in.torrent_answers["a/risky"] = answer
    run(config)
    assert stand_in.added == []
    assert state_of(config)["at_risk"]["models"] == []


@pytest.mark.parametrize("answer", ["not json", "[]", '{"models": 1}', '{"models": [1]}', '{"other": 1}'])
def test_at_risk_list_it_cant_read_adds_nothing_and_keeps_the_last_one(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture, answer: str
) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/first", NEW)
    stand_in.list_at_risk("a/second", NEXT)
    config = config._replace(max_total=GB)
    run(config)
    assert magnets(stand_in) == [NEW]
    state = state_of(config)
    state["at_risk"]["scanned"] = 0
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    stand_in.catalogue_answer = answer
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert any("skipping the torrents at risk" in message for message in caplog.messages)
    assert magnets(stand_in) == [NEW]
    assert [model["repo"] for model in state_of(config)["at_risk"]["models"]] == ["a/first", "a/second"]


def test_at_risk_torrent_it_cant_read_is_tried_again(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    answer = stand_in.torrent_answers.pop("a/risky")
    original = stand_in.answer

    def garbled(path: str, form: dict[str, str]) -> tuple[HTTPStatus, str, dict[str, str]]:
        if path.startswith("/api/torrents"):
            return HTTPStatus.OK, "not json", {}
        return original(path, form)

    stand_in.answer = garbled  # type: ignore[method-assign]
    run(config)
    assert stand_in.added == []
    assert [model["repo"] for model in state_of(config)["at_risk"]["models"]] == ["a/risky"]
    stand_in.answer = original  # type: ignore[method-assign]
    stand_in.torrent_answers["a/risky"] = answer
    run(config)
    assert magnets(stand_in) == [NEW]


def test_a_poll_with_at_risk_and_no_terms_still_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tw, "HEARTBEAT", tmp_path / "heartbeat")
    ran = []
    monkeypatch.setattr(tw.Watch, "run", lambda _self: ran.append(1))
    tw.poll(
        {
            "TORRENT_WATCH_AT_RISK": "true",
            "TORRENT_WATCH_INDEX_URL": "https://i",
            "TORRENT_WATCH_STATE_FILE": str(tmp_path / "state.json"),
        }
    )
    assert ran == [1]


def test_info_hash_is_the_sha1_of_the_info_dictionary_as_written() -> None:
    info = b"d6:lengthi1e4:name1:a12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"e"
    torrent = b"d8:announce1:u4:info" + info + b"8:url-list1:we"
    assert tw.info_hash(torrent) == hashlib.sha1(info).hexdigest()  # noqa: S324 # BitTorrent's v1 infohash


@pytest.mark.parametrize(
    "torrent",
    [b"", b"x", b"le", b"d4:infoi1", b"d4:info5:ab", b"d3:fooi1ee", b"di1ei2ee", b"d4:infod1:ae", b"d4:infoxe"],
)
def test_a_torrent_file_this_cant_read_raises(torrent: bytes) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 # what matters is that it raises, whichever way it is broken
        tw.info_hash(torrent)


def rescan(config: tw.Config) -> None:
    state = state_of(config)
    state["at_risk"]["scanned"] = 0
    config.state_file.write_text(json.dumps(state), encoding="utf-8")


def test_at_risk_without_a_torrent_file_waits_until_the_index_has_one(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/bare", NEW)
    stand_in.serve_torrent("a/bare", NEW, file=False)
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
        rescan(config)
        run(config)
    assert stand_in.added == []
    assert sum("no .torrent on the index" in message for message in caplog.messages) == 1
    # Its listing has nothing to build a torrent from, so that was tried once and not again.
    assert state_of(config)["done"][stand_in.real[NEW]]["why"] == "rebuild failed"
    stand_in.serve_torrent("a/bare", NEW)
    rescan(config)
    run(config)
    assert magnets(stand_in) == [NEW]


@pytest.mark.parametrize("path", ["/elsewhere/x.torrent", f"/api/torrents/{'f' * 40}.torrent", 5])
def test_at_risk_takes_no_torrent_file_from_anywhere_else(at_risk: tuple[Site, tw.Config], path: object) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    stand_in.torrent_answers["a/risky"]["torrent"] = path
    run(config)
    assert stand_in.added == []
    assert not any(request.endswith(".torrent") for request in stand_in.requests)


@pytest.mark.parametrize("content", [b"not a torrent", make_torrent(NEXT), b"l" * 5000])
def test_at_risk_leaves_out_a_torrent_file_that_isnt_the_listed_torrent(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture, content: bytes
) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    stand_in.torrent_files[stand_in.real[NEW]] = content
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert stand_in.added == []
    assert any("isn't the torrent its listing names" in message for message in caplog.messages)
    assert state_of(config)["at_risk"]["models"] == []


def test_at_risk_tries_again_when_the_torrent_file_cant_be_fetched(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    torrent = stand_in.torrent_files.pop(stand_in.real[NEW])
    run(config)
    assert stand_in.added == []
    stand_in.torrent_files[stand_in.real[NEW]] = torrent
    run(config)
    assert magnets(stand_in) == [NEW]


def no_metadata(**fields: object) -> dict[str, object]:
    return {"category": tw.CATEGORY, "total_size": -1, "amount_left": 0, "state": "metaDL", **fields}


def age_waiting(config: tw.Config, seconds: float) -> None:
    state = state_of(config)
    state["no_metadata"] = {key: when - seconds for key, when in state["no_metadata"].items()}
    config.state_file.write_text(json.dumps(state), encoding="utf-8")


def test_its_torrent_with_no_metadata_for_a_day_is_removed_and_its_room_freed(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/stuck", NEW))
    stand_in.models["a/stuck"] = {"license": "mit", "sizeBytes": 90 * GB}
    run(config)
    stand_in.torrents = {NEW: no_metadata(), "e" * 40: no_metadata(category="")}
    run(config)
    age_waiting(config, tw.GIVE_UP_SECONDS - 60)
    run(config)
    assert stand_in.removed == []
    age_waiting(config, 120)
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert stand_in.removed == [{"hashes": NEW, "deleteFiles": "false"}]
    assert state_of(config)["done"][NEW]["why"] == "gave up: no metadata"
    assert state_of(config)["no_metadata"] == {}
    assert any("found no metadata in a day" in message for message in caplog.messages)


def test_a_torrent_that_finds_its_metadata_isnt_removed(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.torrents = {NEW: no_metadata()}
    run(config)
    stand_in.torrents = {NEW: no_metadata(total_size=GB, state="downloading")}
    run(config)
    assert state_of(config)["no_metadata"] == {}
    stand_in.torrents = {NEW: no_metadata()}
    run(config)
    age_waiting(config, 60)
    run(config)
    assert stand_in.removed == []


def test_failing_to_remove_a_torrent_with_no_metadata_is_tried_again(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    stand_in.torrents = {NEW: no_metadata()}
    run(config)
    age_waiting(config, tw.GIVE_UP_SECONDS)
    stand_in.remove_status = HTTPStatus.BAD_GATEWAY
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert any("can't remove 1 torrents" in message for message in caplog.messages)
    assert NEW in state_of(config)["no_metadata"]
    stand_in.remove_status = HTTPStatus.OK
    run(config)
    assert [form["hashes"] for form in stand_in.removed] == [NEW, NEW]
    assert state_of(config)["no_metadata"] == {}


def test_a_model_given_up_on_is_taken_again_once_the_index_has_its_torrent_file(
    at_risk: tuple[Site, tw.Config],
) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    real = stand_in.real[NEW]
    state = {"done": {real: {"repo": "a/risky", "why": "gave up: no metadata", "size": GB}}}
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    assert magnets(stand_in) == [NEW]


def calls(stand_in: Site, name: str) -> list[dict[str, str]]:
    return [form for called, form in stand_in.calls if called == name]


def test_a_model_with_no_torrent_file_gets_a_torrent_built_here_ready_to_submit(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = at_risk
    files = stand_in.needs_torrent(config.index_url)
    caplog.set_level(logging.INFO, logger="torrent-watch")
    run(config)
    folder = config.files_dir / "a__bare"
    assert {path: (folder / path).read_bytes() for path in files} == files
    assert calls(stand_in, "torrentcreator/addTask") == [
        {
            "sourcePath": "/downloads/watch/a__bare",
            "format": "hybrid",
            "startSeeding": "true",
            "comment": f"a/bare at {REV}, every file checked against its source",
            "trackers": "udp://tracker.example:6969/announce",
            "urlSeeds": f"{config.index_url}/api/ws/a/bare/{REV}/",
        }
    ]
    assert stand_in.added == []
    run(config)
    ready = config.files_dir / tw.READY_FOLDER
    assert (ready / "a__bare.torrent").read_bytes() == stand_in.created
    sheet = (ready / "a__bare.txt").read_text(encoding="utf-8")
    infohash = tw.info_hash(stand_in.created)
    for expected in ("a/bare", REV, "mit", f"{config.index_url}/submit", infohash, hashlib.sha256(b"{}").hexdigest()):
        assert expected in sheet
    assert calls(stand_in, "torrents/setCategory") == [{"hashes": KEY, "category": tw.CATEGORY}]
    assert calls(stand_in, "torrentcreator/deleteTask") == [{"taskID": "task-1"}]
    state = state_of(config)
    assert state["done"][infohash] == {"repo": "a/bare", "why": "added", "size": GB, "built": True}
    assert state["done"][stand_in.real[NEW]]["why"] == "built here"
    assert state["rebuild"] is None
    assert any("ready to submit" in message for message in caplog.messages)
    run(config)
    assert len(calls(stand_in, "torrentcreator/addTask")) == 1


def test_a_rebuild_carries_on_across_polls_from_where_it_stopped(
    at_risk: tuple[Site, tw.Config], monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in, config = at_risk
    monkeypatch.setattr(tw, "CHUNK_BYTES", 1000)
    files = stand_in.needs_torrent(config.index_url)
    run(config._replace(work_seconds=0))
    part = config.files_dir / "a__bare" / "model.bin.part"
    assert part.stat().st_size == 1000
    assert not (config.files_dir / "a__bare" / "model.bin").exists()
    assert calls(stand_in, "torrentcreator/addTask") == []
    run(config)
    assert (config.files_dir / "a__bare" / "model.bin").read_bytes() == files["model.bin"]
    assert "bytes=1000-" in stand_in.ranges
    assert len(calls(stand_in, "torrentcreator/addTask")) == 1


def test_a_rebuild_starts_a_file_again_when_the_source_ignores_the_range(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    files = stand_in.needs_torrent(config.index_url)
    folder = config.files_dir / "a__bare"
    folder.mkdir(parents=True)
    (folder / "model.bin.part").write_bytes(b"junk from an earlier try")
    stand_in.honour_range = False
    run(config)
    assert (folder / "model.bin").read_bytes() == files["model.bin"]


def test_a_part_file_already_complete_is_checked_and_kept(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    files = stand_in.needs_torrent(config.index_url)
    folder = config.files_dir / "a__bare"
    folder.mkdir(parents=True)
    (folder / "model.bin.part").write_bytes(files["model.bin"])
    run(config)
    assert (folder / "model.bin").read_bytes() == files["model.bin"]
    assert f"bytes={len(files['model.bin'])}-" in stand_in.ranges


def test_a_file_that_doesnt_match_its_checksum_ends_the_rebuild(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    stand_in.source_files[f"/a/bare/resolve/{REV}/model.bin"] = b"something else"
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert calls(stand_in, "torrentcreator/addTask") == []
    assert not (config.files_dir / "a__bare" / "model.bin.part").exists()
    state = state_of(config)
    assert state["rebuild"] is None
    entry = state["done"][stand_in.real[NEW]]
    assert entry["why"] == "rebuild failed"
    assert entry["error"].startswith("model.bin from the source doesn't match")
    # The model is still at risk with no .torrent file, but its rebuild isn't tried again at the next scan.
    rescan(config)
    run(config)
    assert calls(stand_in, "torrentcreator/addTask") == []
    assert state_of(config)["done"][stand_in.real[NEW]]["why"] == "rebuild failed"


def test_a_source_that_fails_for_now_is_tried_again(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    stand_in.source_status = HTTPStatus.BAD_GATEWAY
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert any("carries on at the next poll" in message for message in caplog.messages)
    assert state_of(config)["rebuild"]["repo"] == "a/bare"
    stand_in.source_status = HTTPStatus.OK
    run(config)
    assert len(calls(stand_in, "torrentcreator/addTask")) == 1


@pytest.mark.parametrize(
    "files",
    [
        [{"path": "../escape", "sha256": "0" * 64}],
        [{"path": "/etc/passwd", "sha256": "0" * 64}],
        [{"path": "a\\b", "sha256": "0" * 64}],
        [{"path": "a//b", "sha256": "0" * 64}],
        [{"path": "model.bin", "sha256": "not a checksum"}],
        [{"path": 5, "sha256": "0" * 64}],
        [],
    ],
)
def test_a_listing_that_isnt_safe_to_build_from_is_left_alone(
    at_risk: tuple[Site, tw.Config], tmp_path: Path, files: list[dict]
) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    stand_in.torrent_answers["a/bare"]["files"] = files
    run(config)
    assert calls(stand_in, "torrentcreator/addTask") == []
    assert not (tmp_path / "escape").exists()
    entry = state_of(config)["done"][stand_in.real[NEW]]
    assert entry["why"] == "rebuild failed"
    assert entry["error"].startswith("the listing of a/bare")


@pytest.mark.parametrize("change", [{"torrent": f"/api/torrents/{'f' * 40}.torrent"}, {"seeders": 1}])
def test_a_model_whose_listing_changed_isnt_built_and_is_looked_at_again(
    at_risk: tuple[Site, tw.Config], change: dict
) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    real = stand_in.real[NEW]
    state = {"done": {real: {"repo": "a/bare", "why": tw.NEEDS_TORRENT, "license": "mit", "size": GB}}}
    state["at_risk"] = {"scanned": time.time(), "models": []}
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    stand_in.torrent_answers["a/bare"].update(change)
    run(config)
    assert state_of(config)["done"][real]["why"] == "check again"
    assert state_of(config)["rebuild"] is None


def test_a_rebuild_over_the_limits_isnt_started(at_risk: tuple[Site, tw.Config]) -> None:
    _, config = at_risk
    big = {"repo": "a/big", "why": tw.NEEDS_TORRENT, "license": "mit", "size": 600 * GB}
    state = {"done": {"9" * 40: big}, "at_risk": {"scanned": time.time(), "models": []}}
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    assert state_of(config)["rebuild"] is None


def test_a_rebuild_counts_toward_the_limits_while_it_runs(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/small", NEXT)
    job = {
        "repo": "a/bare", "license": "mit", "size": 500 * GB, "name": "a__bare", "old": "9" * 40, "revision": REV,
        "files": [], "verified": [], "trackers": [], "web_seed": None, "magnet": "", "task": None,
    }  # fmt: skip
    config.state_file.write_text(json.dumps({"rebuild": job}), encoding="utf-8")
    run(config)
    assert stand_in.added == []
    assert calls(stand_in, "torrentcreator/addTask")[-1]["sourcePath"] == "/downloads/watch/a__bare"
    # Once qBittorrent is making it, its seeding copy counts as qBittorrent's own, so there is room again here.
    run(config)
    assert magnets(stand_in) == [NEXT]


@pytest.mark.parametrize(("status", "torrent"), [("Failed", None), ("", None), ("Finished", b"not a torrent")])
def test_a_torrent_qbittorrent_couldnt_make_ends_the_rebuild(
    at_risk: tuple[Site, tw.Config], status: str, torrent: bytes | None
) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    stand_in.creation_status = status
    if torrent:
        stand_in.created = torrent
        stand_in.created_seeding = False
    run(config)
    run(config)
    entry = state_of(config)["done"][stand_in.real[NEW]]
    assert state_of(config)["rebuild"] is None
    assert entry["why"] == "rebuild failed"
    assert entry["error"].startswith("qBittorrent")


def test_a_torrent_still_being_made_is_looked_at_again(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    stand_in.creation_status = "Running"
    run(config)
    run(config)
    assert state_of(config)["rebuild"]["task"] == "task-1"
    stand_in.creation_status = "Finished"
    run(config)
    assert state_of(config)["rebuild"] is None


def test_a_torrent_made_but_not_seeding_is_still_left_ready_to_submit(
    at_risk: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    stand_in.created_seeding = False
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
        run(config)
    assert (config.files_dir / tw.READY_FOLDER / "a__bare.torrent").exists()
    assert calls(stand_in, "torrents/setCategory") == []
    assert any("isn't seeding it" in message for message in caplog.messages)


def test_qbittorrent_not_starting_the_torrent_is_tried_again(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    original = stand_in.qbittorrent_answer

    def garbled(url: urllib.parse.SplitResult, form: dict[str, str]) -> tuple[HTTPStatus, bytes | str, dict[str, str]]:
        if url.path.endswith("addTask"):
            return HTTPStatus.OK, "not json", {}
        return original(url, form)

    stand_in.qbittorrent_answer = garbled  # type: ignore[method-assign]
    run(config)
    assert state_of(config)["rebuild"]["task"] is None
    stand_in.qbittorrent_answer = original  # type: ignore[method-assign]
    run(config)
    assert state_of(config)["rebuild"]["task"] == "task-1"


def test_no_rebuild_without_at_risk_or_for_an_uncensored_name(watching: tuple[Site, tw.Config]) -> None:
    _, config = watching
    state = state_of(config)
    state["done"]["9" * 40] = {"repo": "a/bare", "why": tw.NEEDS_TORRENT, "license": "mit", "size": GB}
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    assert state_of(config)["rebuild"] is None
    state = state_of(config)
    state["done"]["9" * 40]["repo"] = "a/model-uncensored"
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config._replace(at_risk=True))
    assert state_of(config)["rebuild"] is None


def test_download_refuses_a_file_larger_than_its_limit(site: tuple[Site, str], tmp_path: Path) -> None:
    stand_in, url = site
    stand_in.source_files["/a/b/resolve/x/big"] = b"x" * 100
    with pytest.raises(tw.JobError, match="larger than the whole model"):
        tw.download(f"{url}/a/b/resolve/x/big", tmp_path / "big", "0" * 64, time.time() + 60, 50)


@pytest.mark.parametrize("path", ["", ".", "a/../b", "a/./b", "a\\b", "a\x00", None])
def test_a_path_that_could_leave_its_folder_is_refused(path: object) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 # any refusal will do
        tw.safe_relative(path)


def stalled(stand_in: Site, config: tw.Config, data: bytes) -> Path:
    """One of its torrents, a/stuck, stalled at 99.5 % with model.bin unfinished; return where model.bin is."""
    stand_in.torrent_answers["a/stuck"] = {"magnet": f"magnet:?xt=urn:btih:{NEW}", "seeders": 0, "infohash": NEW}
    stand_in.source_listing(config.index_url, "a/stuck", {"model.bin": data, "README.md": b"read me"})
    stand_in.torrents = {
        KEY: {"infohash_v1": NEW, "category": tw.CATEGORY, "total_size": 2000, "amount_left": 10, "progress": 0.995}
    }
    stand_in.file_lists[KEY] = [
        {"name": "a__stuck/model.bin", "size": len(data), "progress": 0.9},
        {"name": "a__stuck/README.md", "size": 7, "progress": 1},
        {"name": "a__stuck/.pad/123", "size": 123, "progress": 0},
    ]
    state = {"done": {NEW: {"repo": "a/stuck", "why": "added", "size": 2000}}}
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    target = config.files_dir / "a__stuck" / "model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"partial")
    return target


def age_near_done(config: tw.Config, seconds: float) -> None:
    state = state_of(config)
    for entry in state["near_done"].values():
        entry["since"] -= seconds
    config.state_file.write_text(json.dumps(state), encoding="utf-8")


def test_its_torrent_stalled_just_short_of_done_gets_its_unfinished_files_from_the_source_once(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    data = b"weights" * 300
    target = stalled(stand_in, config, data)
    run(config)
    age_near_done(config, tw.STALL_SECONDS - 60)
    run(config)
    assert calls(stand_in, "torrents/recheck") == []
    age_near_done(config, 120)
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
    assert target.read_bytes() == data
    assert stand_in.stopped == [KEY]
    assert calls(stand_in, "torrents/recheck") == [{"hashes": KEY}]
    assert calls(stand_in, "torrents/start") == []
    assert any("repaired a/stuck" in message for message in caplog.messages)
    state = state_of(config)
    assert state["repaired"] == [KEY]
    assert state["repair"] is None
    assert state["restart"] == [KEY]
    # qBittorrent drops a start sent while it checks, so it waits for the check to end, and repeats until it holds.
    stand_in.torrents[KEY].update({"state": "checkingUP", "amount_left": 0, "progress": 1})
    run(config)
    assert calls(stand_in, "torrents/start") == []
    stand_in.torrents[KEY]["state"] = "stoppedUP"
    run(config)
    run(config)
    assert calls(stand_in, "torrents/start") == [{"hashes": KEY}, {"hashes": KEY}]
    stand_in.torrents[KEY]["state"] = "stalledUP"
    run(config)
    assert state_of(config)["restart"] == []
    age_near_done(config, tw.STALL_SECONDS)
    run(config)
    assert len(calls(stand_in, "torrents/recheck")) == 1
    stand_in.torrents = {}
    state = state_of(config)
    state["restart"] = [KEY]
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    assert state_of(config)["restart"] == []


def test_a_torrent_still_moving_isnt_repaired(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stalled(stand_in, config, b"weights")
    run(config)
    age_near_done(config, tw.STALL_SECONDS)
    stand_in.torrents[KEY]["amount_left"] = 5
    run(config)
    stand_in.torrents = {}
    run(config)
    assert state_of(config)["near_done"] == {}
    assert calls(stand_in, "torrents/recheck") == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"listing": {"infohash": "f" * 40}}, "its listing names another torrent now"),
        ({"listing": {"files": []}}, "isn't one to build a torrent from"),
        ({"file": "a__stuck/other.bin"}, "its listing has no checksum for other.bin"),
        ({"file": "../outside"}, "a path that leaves its folder"),
        ({"done": {}}, "it isn't one of the torrents this added"),
    ],
)
def test_a_torrent_that_cant_be_repaired_is_left_as_it_is_and_not_tried_again(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture, change: dict, expected: str
) -> None:
    stand_in, config = watching
    stalled(stand_in, config, b"weights")
    stand_in.torrent_answers["a/stuck"].update(change.get("listing", {}))
    if "file" in change:
        stand_in.file_lists[KEY][0]["name"] = change["file"]
    if "done" in change:
        state = state_of(config)
        state["done"] = change["done"]
        config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    age_near_done(config, tw.STALL_SECONDS)
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert any(expected in message for message in caplog.messages)
    assert stand_in.stopped == []
    assert state_of(config)["repaired"] == [KEY]


@pytest.mark.parametrize("call", ["torrents/recheck", "torrents/stop", "torrents/files", "sync/maindata"])
def test_a_session_that_ended_mid_poll_signs_in_again_and_carries_on(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture, call: str
) -> None:
    stand_in, config = watching
    data = b"weights" * 300
    target = stalled(stand_in, config, data)
    run(config)
    age_near_done(config, tw.STALL_SECONDS)
    stand_in.session_ends.add(call)
    logins = stand_in.logins
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        run(config)
    assert stand_in.logins == logins + 2
    assert any("signing in again" in message for message in caplog.messages)
    assert target.read_bytes() == data
    assert calls(stand_in, "torrents/recheck") == [{"hashes": KEY}]
    assert state_of(config)["repair"] is None


def test_a_403_that_signing_in_again_doesnt_cure_is_still_an_error(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    stalled(stand_in, config, b"weights")
    run(config)
    age_near_done(config, tw.STALL_SECONDS)
    stand_in.always_forbidden.add("torrents/recheck")
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert any("carries on at the next poll" in message and "403" in message for message in caplog.messages)
    assert state_of(config)["repair"] is not None
    stand_in.always_forbidden.clear()
    run(config)
    assert state_of(config)["repair"] is None


def test_a_session_that_ended_before_a_file_upload_signs_in_again(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/risky", NEW)
    stand_in.session_ends.add("torrents/add")
    run(config)
    assert magnets(stand_in) == [NEW]


def test_a_session_that_ended_before_fetching_a_built_torrent_signs_in_again(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.needs_torrent(config.index_url)
    run(config)
    stand_in.session_ends.add("torrentcreator/torrentFile")
    run(config)
    assert (config.files_dir / tw.READY_FOLDER / "a__bare.torrent").read_bytes() == stand_in.created


def test_a_repair_whose_file_doesnt_match_is_given_up(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    target = stalled(stand_in, config, b"weights")
    stand_in.source_files[f"/a/stuck/resolve/{REV}/model.bin"] = b"weightz"
    run(config)
    age_near_done(config, tw.STALL_SECONDS)
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert any("giving up the repair of a/stuck" in message for message in caplog.messages)
    assert target.read_bytes() == b"partial"
    assert state_of(config)["repair"] is None


def test_a_repair_listing_that_cant_be_fetched_is_tried_again(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stalled(stand_in, config, b"weights")
    run(config)
    age_near_done(config, tw.STALL_SECONDS)
    stand_in.index_status = HTTPStatus.BAD_GATEWAY
    run(config)
    assert state_of(config)["repaired"] == []
    stand_in.index_status = HTTPStatus.OK
    run(config)
    assert state_of(config)["repaired"] == [KEY]


@pytest.mark.parametrize("free", [0, None, "1000"])
def test_no_free_space_figure_means_nothing_is_added_or_stopped(
    watching: tuple[Site, tw.Config], free: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in, config = watching
    monkeypatch.setattr(stand_in, "free", free)
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit", "sizeBytes": GB}
    with pytest.raises(tw.WatchError, match="free space"):
        run(config)
    assert stand_in.added == []
    assert stand_in.stopped == []


@pytest.mark.parametrize("refusal", [HTTPStatus.UNAUTHORIZED, HTTPStatus.OK])
def test_a_wrong_password_stops_the_poll_before_the_index_is_asked(
    site: tuple[Site, str], tmp_path: Path, refusal: HTTPStatus
) -> None:
    stand_in, url = site
    stand_in.password = "other"  # noqa: S105 # a stand-in's
    stand_in.refusal = refusal
    config = make_config(url, tmp_path, "qwen")
    with pytest.raises(tw.WatchError, match="refused the sign-in"):
        run(config)
    assert not any(path.startswith("/s/") for path in stand_in.requests)
    assert not config.state_file.exists()


def test_qbittorrent_banning_the_sign_in_is_reported_as_it_is(site: tuple[Site, str], tmp_path: Path) -> None:
    stand_in, url = site
    stand_in.password = "other"  # noqa: S105 # a stand-in's
    stand_in.refusal = HTTPStatus.FORBIDDEN
    with pytest.raises(tw.WatchError, match="403"):
        run(make_config(url, tmp_path, "qwen"))


@pytest.mark.parametrize("content", [None, " \n"])
def test_a_missing_or_empty_password_file_stops_the_poll(
    site: tuple[Site, str], tmp_path: Path, content: str | None
) -> None:
    _, url = site
    config = make_config(url, tmp_path, "qwen")
    if content is None:
        config.password_file.unlink()
    else:
        config.password_file.write_text(content, encoding="utf-8")
    with pytest.raises(tw.WatchError, match="password"):
        run(config)


def test_qbittorrent_refusing_a_magnet_leaves_it_for_the_next_poll(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit", "sizeBytes": GB}
    for answer in ("Fails.", '{"failure_count":1,"success_count":0}'):
        stand_in.add_answer = answer
        run(config)
        assert NEW not in state_of(config)["done"]
    stand_in.add_answer = "Ok."
    run(config)
    assert state_of(config)["done"][NEW]["why"] == "added"


def test_an_index_error_at_a_terms_first_run_records_nothing(site: tuple[Site, str], tmp_path: Path) -> None:
    stand_in, url = site
    stand_in.search["qwen"] = [row("a/old", OLD)]
    stand_in.index_status = HTTPStatus.BAD_GATEWAY
    config = make_config(url, tmp_path, "qwen")
    run(config)
    assert state_of(config)["terms"] == []
    stand_in.index_status = HTTPStatus.OK
    run(config)
    assert state_of(config)["terms"] == ["qwen"]


def test_a_failed_details_lookup_leaves_the_torrent_for_the_next_poll(watching: tuple[Site, tw.Config]) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit", "sizeBytes": GB}
    original = stand_in.answer

    def fail_details(path: str, form: dict[str, str]) -> tuple[HTTPStatus, str, dict[str, str]]:
        if path.startswith("/api/models"):
            return HTTPStatus.BAD_GATEWAY, "", {}
        return original(path, form)

    stand_in.answer = fail_details  # type: ignore[method-assign]
    run(config)
    assert stand_in.added == []
    stand_in.answer = original  # type: ignore[method-assign]
    run(config)
    assert magnets(stand_in) == [NEW]


def test_a_new_term_records_its_matches_without_dropping_what_another_term_is_waiting_on(
    watching: tuple[Site, tw.Config],
) -> None:
    stand_in, config = watching
    stand_in.search["qwen"].append(row("a/new", NEW))
    stand_in.models["a/new"] = {"license": "mit"}
    run(config)
    stand_in.search["other"] = [row("a/new", NEW), row("a/next", NEXT)]
    config = config._replace(terms=(tw.parse_term("qwen"), tw.parse_term("other")))
    run(config)
    state = state_of(config)
    assert state["terms"] == ["qwen", "other"]
    assert NEW not in state["done"]
    assert state["done"][NEXT]["why"] == "listed before"
    stand_in.models["a/new"]["sizeBytes"] = GB
    run(config)
    assert magnets(stand_in) == [NEW]


def test_a_term_with_a_slash_keeps_only_that_authors_models(site: tuple[Site, str], tmp_path: Path) -> None:
    stand_in, url = site
    stand_in.search["someone"] = [row("Someone/real", NEW), row("other/someone_copy", NEXT)]
    stand_in.models["Someone/real"] = {"license": "mit", "sizeBytes": GB}
    config = make_config(url, tmp_path, "someone/")
    run(config)
    stand_in.search["someone"].clear()
    assert state_of(config)["done"].keys() == {NEW}
    # After the first run, the other author's new models are still left out.
    stand_in.search["someone"] += [row("Someone/next", OLD), row("other/next", "4" * 40)]
    stand_in.models["Someone/next"] = {"license": "mit", "sizeBytes": GB}
    run(config)
    assert magnets(stand_in) == [OLD]


@pytest.mark.parametrize(
    "repo",
    [
        "a/Qwen3.8-27B-Uncensored-GGUF",
        "a/model-ABLITERATED",
        "a/model-heretic-v2",
        "a/Decensored-model",
        "a/model-OBLITERATUS",
        "a/Obliterated_model",
        "uncensored-labs/model",
    ],
)
def test_an_uncensored_model_is_never_added_whichever_way_it_is_found(
    site: tuple[Site, str], tmp_path: Path, repo: str
) -> None:
    stand_in, url = site
    config = make_config(url, tmp_path, "qwen")._replace(at_risk=True)
    run(config)
    stand_in.search["qwen"] = [row(repo, NEW)]
    stand_in.models[repo] = {"license": "mit", "sizeBytes": GB}
    stand_in.list_at_risk(repo, NEW)
    state = state_of(config)
    state["at_risk"]["scanned"] = 0
    config.state_file.write_text(json.dumps(state), encoding="utf-8")
    run(config)
    assert stand_in.added == []
    assert state_of(config)["at_risk"]["models"] == []
    assert NEW not in state_of(config)["pending"]


def test_a_similar_name_isnt_taken_for_an_uncensored_one(at_risk: tuple[Site, tw.Config]) -> None:
    stand_in, config = at_risk
    stand_in.list_at_risk("a/censorship-classifier", NEW)
    run(config)
    assert magnets(stand_in) == [NEW]


def test_a_search_with_many_rows_warns_that_the_term_is_broad(
    watching: tuple[Site, tw.Config], caplog: pytest.LogCaptureFixture
) -> None:
    stand_in, config = watching
    stand_in.search["qwen"] = [row(f"a/{number}", f"{number:040x}") for number in range(tw.BROAD_TERM_ROWS)]
    with caplog.at_level(logging.WARNING, logger="torrent-watch"):
        run(config)
    assert any("narrow it" in message for message in caplog.messages)


def test_search_words_are_joined_and_escaped(site: tuple[Site, str], tmp_path: Path) -> None:
    stand_in, url = site
    run(make_config(url, tmp_path, "qwen a&b"))
    assert "/s/qwen+a%26b" in stand_in.requests


@pytest.mark.parametrize(
    "text",
    ["<!DOCTYPE html><html>a page</html>", "", "someone/model 1 GB 2 seeds no link here\n"],
)
def test_a_page_that_is_neither_results_nor_no_match_is_an_error(text: str) -> None:
    with pytest.raises(tw.WatchError, match="can't read"):
        tw.parse_search(text)


@pytest.mark.parametrize(
    "link",
    [
        "magnet:?xt=urn:btih:short",
        "magnet:?xt=urn:btmh:" + "1" * 68,
        "magnet:?dn=first&xt=urn:btih:" + "1" * 40,
    ],
)
def test_only_a_plain_magnet_link_is_taken(link: str, caplog: pytest.LogCaptureFixture) -> None:
    good = row("a/good", OLD)
    assert [found.repo for found in tw.parse_search(f"a/bad  1 GB  1 seeds  {link}\n{good}\n")] == ["a/good"]
    assert any("a/bad" in message for message in caplog.messages)


def test_a_search_row_reads_as_its_parts() -> None:
    found = tw.parse_search(row("a/model-1.5_B", "A" * 40))
    assert found == [tw.Row("a/model-1.5_B", "a" * 40, f"magnet:?xt=urn:btih:{'A' * 40}&tr=udp%3A%2F%2Ftracker%3A1")]


def test_details_that_arent_the_expected_shape_are_unknown() -> None:
    for text in ("not json", "[]", '{"models": 1}', '{"models": [1]}', '{"other": 1}'):
        assert tw.Index("https://index", lambda _url, answer=text: answer).details("a/b") == tw.Details(None, None)


def test_settings_defaults() -> None:
    config = tw.load_config({"TORRENT_WATCH_TERMS": " qwen gguf , someone/ ,", "TORRENT_WATCH_INDEX_URL": "https://i/"})
    assert config.terms == (tw.Term("qwen gguf", ("qwen", "gguf"), None), tw.Term("someone/", ("someone",), "someone"))
    assert config.index_url == "https://i"
    assert config.qbittorrent_url == "http://qbittorrent-direct:8091"
    assert config.username == "admin"
    assert (config.max_torrent, config.max_total, config.min_free) == (100 * GB, 500 * GB, 500 * GB)


def test_settings_left_empty_by_compose_take_the_defaults() -> None:
    config = tw.load_config({"TORRENT_WATCH_MAX_TORRENT_GB": "", "TORRENT_WATCH_QBITTORRENT_USERNAME": ""})
    assert config.max_torrent == 100 * GB
    assert config.username == "admin"
    assert config.terms == ()


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"TORRENT_WATCH_TERMS": "a"}, "must be an https"),
        ({"TORRENT_WATCH_TERMS": "a", "TORRENT_WATCH_INDEX_URL": "http://i"}, "must be an https"),
        ({"TORRENT_WATCH_TERMS": "/", "TORRENT_WATCH_INDEX_URL": "https://i"}, "names no author"),
        ({"TORRENT_WATCH_AT_RISK": "true"}, "must be an https"),
        ({"TORRENT_WATCH_AT_RISK": "yes"}, "true or false"),
        ({"TORRENT_WATCH_MIN_FREE_GB": "0"}, "above zero"),
        ({"TORRENT_WATCH_MAX_TOTAL_GB": "lots"}, "above zero"),
        ({"TORRENT_WATCH_MAX_TORRENT_GB": "-1"}, "above zero"),
    ],
)
def test_settings_that_cant_be_right_are_refused(settings: dict[str, str], expected: str) -> None:
    with pytest.raises(tw.WatchError, match=expected):
        tw.load_config(settings)


@pytest.mark.parametrize("content", ["{broken", "[1, 2]"])
def test_a_damaged_state_file_starts_again(tmp_path: Path, content: str) -> None:
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    assert tw.load_state(path) == {
        "terms": [],
        "done": {},
        "pending": {},
        "forced": [],
        "at_risk": {},
        "no_metadata": {},
        "near_done": {},
        "repaired": [],
        "rebuild": None,
        "repair": None,
        "restart": [],
    }


def test_saving_the_state_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    tw.save_state(path, {"terms": ["a"]})
    assert [item.name for item in tmp_path.iterdir()] == ["state.json"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"terms": ["a"]}


def test_fetch_reports_an_unreachable_index() -> None:
    with pytest.raises(tw.WatchError, match="can't read the index"):
        tw.fetch("http://127.0.0.1:9/")


def test_a_poll_with_no_terms_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tw, "HEARTBEAT", tmp_path / "heartbeat")
    with caplog.at_level(logging.INFO, logger="torrent-watch"):
        tw.poll({})
    assert any("nothing to do" in message for message in caplog.messages)
    assert (tmp_path / "heartbeat").exists()


def test_a_poll_logs_settings_it_cant_use_and_stays_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tw, "HEARTBEAT", tmp_path / "heartbeat")
    tw.poll({"TORRENT_WATCH_MIN_FREE_GB": "0"})
    assert any("above zero" in message for message in caplog.messages)


def test_a_poll_survives_anything_unexpected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tw, "HEARTBEAT", tmp_path / "heartbeat")

    def broken(_self: tw.Watch) -> None:
        message = "unexpected"
        raise RuntimeError(message)

    monkeypatch.setattr(tw.Watch, "run", broken)
    tw.poll(
        {
            "TORRENT_WATCH_TERMS": "a",
            "TORRENT_WATCH_INDEX_URL": "https://i",
            "TORRENT_WATCH_STATE_FILE": str(tmp_path / "state.json"),
        }
    )
    assert any("next one tries again" in message for message in caplog.messages)


def test_main_runs_one_poll_with_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(tw, "HEARTBEAT", tmp_path / "heartbeat")
    monkeypatch.delenv("TORRENT_WATCH_TERMS", raising=False)
    monkeypatch.setattr(tw.signal, "signal", lambda *_args: None)
    assert tw.main(["--once"]) == 0
    capsys.readouterr()
    assert (tmp_path / "heartbeat").exists()


def test_main_keeps_polling_until_it_is_told_to_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tw, "HEARTBEAT", tmp_path / "heartbeat")
    monkeypatch.delenv("TORRENT_WATCH_TERMS", raising=False)
    handlers = []
    monkeypatch.setattr(tw.signal, "signal", lambda _signal, handler: handlers.append(handler))
    polls = []
    monkeypatch.setattr(tw, "poll", lambda _env: polls.append(1))

    def sleep(seconds: int) -> None:
        assert seconds == tw.POLL_SECONDS
        if len(polls) == 3:
            handlers[0](15, None)

    monkeypatch.setattr(tw.time, "sleep", sleep)
    with pytest.raises(SystemExit) as stopped:
        tw.main([])
    assert stopped.value.code == 0
    assert len(polls) == 3


def test_the_health_check_follows_the_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr(tw, "HEARTBEAT", heartbeat)
    assert tw.main(["--healthcheck"]) == 1
    heartbeat.touch()
    assert tw.main(["--healthcheck"]) == 0
    stale = time.time() - 2 * tw.POLL_SECONDS - 1
    os.utime(heartbeat, (stale, stale))
    assert tw.main(["--healthcheck"]) == 1
