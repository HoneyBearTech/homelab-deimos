from __future__ import annotations

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
        self.catalogue_answer: str | None = None
        self.index_status = HTTPStatus.OK
        self.add_answer = '{"failure_count":0,"success_count":1}'
        self.refusal = HTTPStatus.UNAUTHORIZED

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
        self.torrent_answers[repo] = {"magnet": f"magnet:?xt=urn:btih:{infohash}&ws=https%3A%2F%2Fi", "seeders": 0}

    def answer(self, path: str, form: dict[str, str]) -> tuple[HTTPStatus, str, dict[str, str]]:  # noqa: C901, PLR0911 # one per route
        url = urllib.parse.urlsplit(path)
        self.requests.append(path)
        if url.path.startswith("/s/"):
            term = urllib.parse.unquote_plus(url.path.removeprefix("/s/"))
            lines = self.search.get(term)
            text = "\n".join(lines) + "\n" if lines else f'{tw.NO_MATCH} "{term}" yet.\n'
            return self.index_status, text, {}
        if url.path == "/api/models" and "license=" in url.query:
            return self.catalogue_page(urllib.parse.parse_qs(url.query))
        if url.path == "/api/torrents":
            repo = urllib.parse.parse_qs(url.query)["repo"][0]
            return self.index_status, json.dumps(self.torrent_answers.get(repo, {"magnet": None})), {}
        if url.path == "/api/models":
            repo = urllib.parse.parse_qs(url.query)["q"][0]
            models = [{"id": repo, **self.models[repo]}] if repo in self.models else []
            return self.index_status, json.dumps({"models": models}), {}
        if url.path == "/api/v2/auth/login":
            if form.get("password") == self.password:
                return HTTPStatus.OK, "Ok.", {"Set-Cookie": "SID=session; path=/"}
            return self.refusal, "Fails.", {}
        if url.path == "/api/v2/sync/maindata":
            data = {"torrents": self.torrents, "server_state": {"free_space_on_disk": self.free}}
            return HTTPStatus.OK, json.dumps(data), {}
        if url.path == "/api/v2/torrents/add":
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
            body = text.encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self.respond({})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.respond(dict(urllib.parse.parse_qsl(self.rfile.read(length).decode())))

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
    )


def run(config: tw.Config) -> None:
    client = tw.Qbittorrent(config.qbittorrent_url, config.username, config.password_file)
    tw.Watch(config, tw.Index(config.index_url), client, tw.load_state(config.state_file)).run()


def state_of(config: tw.Config) -> dict:
    return json.loads(config.state_file.read_text(encoding="utf-8"))


def magnets(stand_in: Site) -> list[str]:
    return [form["urls"].split("&")[0].rsplit(":", 1)[1] for form in stand_in.added]


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
    assert state_of(config)["done"][NEW] == {"repo": "a/small", "why": "added", "size": GB}


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
    stand_in.torrents = {"f" * 40: {"infohash_v1": NEW, "category": "", "total_size": GB, "amount_left": 0}}
    run(config)
    assert stand_in.added == []
    assert state_of(config)["done"][NEW]["why"] == "already in qBittorrent"


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
    assert tw.load_state(path) == {"terms": [], "done": {}, "pending": {}, "forced": [], "at_risk": {}}


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
