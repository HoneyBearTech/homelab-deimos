"""Add new torrents from a model index to qBittorrent, within limits.

Every half hour this runs each watch term (``TORRENT_WATCH_TERMS``) through the index's plain-text search and adds what
it hasn't seen before to the qBittorrent in this stack, under a category and folder of its own. What a term matches the
first time it runs is only recorded, so only torrents listed after that are added. A term is words the index searches
for (``qwen gguf``), or one author with a slash after it (``someone/``), which keeps only that author's models.

A torrent is added only if the index names a licence in ``ALLOWED_LICENSES`` and a size, that size is within the limit
per torrent and the limit for the category, and the share keeps the free space asked for once everything still
downloading has finished. Anything it can't tell counts as a no, and is looked at again at the next poll. If the share
falls below that free space anyway, its own torrents that are still downloading are stopped, and stay stopped until
someone starts them again.

Once any torrent in qBittorrent has finished, not only one of its own, the next poll switches it to forced seeding, so
that qBittorrent's queue limits don't stop it seeding. That happens once per torrent: one switched back by hand, or
stopped, stays that way.

Standard library only: compose.yaml runs it in a pinned Python image.
"""

from __future__ import annotations

import argparse
import http.client
import http.cookiejar
import json
import logging
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import FrameType

log = logging.getLogger("torrent-watch")

# Licences that let anyone share a copy, with no terms to accept first.
ALLOWED_LICENSES = frozenset({"apache-2.0", "mit", "openmdw-1.1", "wtfpl"})
CATEGORY = "watch"
SAVE_PATH = "/downloads/watch"
# A finished torrent seeding normally. Having nothing left to download isn't enough: so has a magnet link with no
# metadata yet.
SEEDING_STATES = frozenset({"queuedUP", "stalledUP", "uploading"})
POLL_SECONDS = 1800
HEARTBEAT = Path("/tmp/torrent-watch-heartbeat")  # noqa: S108 # the container's own tmpfs
# A second limit on top of the sizes, in case the index ever lists a run of torrents as new by mistake.
MAX_ADDS_PER_POLL = 3
# The search stops at about 90 rows, best-seeded first, so a new torrent with no seeders can miss a longer list.
BROAD_TERM_ROWS = 75
TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
GB = 1000**3  # as the index counts them
ROW_RE = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)\s.*\s(?P<magnet>magnet:\?\S+)\s*$")
MAGNET_RE = re.compile(r"^magnet:\?xt=urn:btih:(?P<hash>[0-9a-fA-F]{40})(?:&\S*)?$")
NO_MATCH = "No torrents match"


class WatchError(Exception):
    """Something this poll can't go on without; it is logged, and the next poll tries again."""


class Term(NamedTuple):
    """One watch term: the text as written, the words to search for, and the one author it keeps, if any."""

    text: str
    words: tuple[str, ...]
    author: str | None


class Row(NamedTuple):
    """One torrent in a search's results."""

    repo: str
    infohash: str
    magnet: str


class Details(NamedTuple):
    """What the index says about a model; a field it doesn't give is empty."""

    license: str | None
    size: int | None


class Config(NamedTuple):
    """The settings, as ``load_config`` reads them from the environment."""

    terms: tuple[Term, ...]
    index_url: str
    qbittorrent_url: str
    username: str
    password_file: Path
    state_file: Path
    max_torrent: int
    max_total: int
    min_free: int
    pause_seconds: float = 1.0


class Snapshot(NamedTuple):
    """What qBittorrent holds right now, in bytes."""

    free: int
    known: frozenset[str]
    total: int
    left: int
    incomplete: tuple[str, ...]
    seeding: tuple[str, ...]


def parse_term(text: str) -> Term:
    """Read a term as written in the settings."""
    words = tuple(text.split())
    if len(words) == 1 and words[0].endswith("/"):
        author = words[0].rstrip("/").lower()
        if not author:
            message = f"the watch term {text!r} names no author"
            raise WatchError(message)
        return Term(" ".join(words), (author,), author)
    return Term(" ".join(words), words, None)


def gigabytes(env: Mapping[str, str], name: str, default: str) -> int:
    """Read a limit in GB from the settings; none of them can be zero."""
    text = env.get(name) or default
    if not text.isdecimal() or int(text) == 0:
        message = f"{name} must be a whole number of GB above zero, not {text!r}"
        raise WatchError(message)
    return int(text) * GB


def load_config(env: Mapping[str, str]) -> Config:
    """Read the settings; an empty list of terms is fine, and means there's nothing to do."""
    terms = tuple(parse_term(text) for text in env.get("TORRENT_WATCH_TERMS", "").split(",") if text.strip())
    index_url = env.get("TORRENT_WATCH_INDEX_URL", "").rstrip("/")
    if terms and not index_url.startswith("https://"):
        message = f"TORRENT_WATCH_INDEX_URL must be an https:// address, not {index_url!r}"
        raise WatchError(message)
    return Config(
        terms=terms,
        index_url=index_url,
        qbittorrent_url=env.get("TORRENT_WATCH_QBITTORRENT_URL", "http://qbittorrent-direct:8091").rstrip("/"),
        username=env.get("TORRENT_WATCH_QBITTORRENT_USERNAME") or "admin",
        password_file=Path(env.get("TORRENT_WATCH_QBITTORRENT_PASSWORD_FILE", "/run/secrets/qbittorrent_password")),
        state_file=Path(env.get("TORRENT_WATCH_STATE_FILE", "/config/state.json")),
        max_torrent=gigabytes(env, "TORRENT_WATCH_MAX_TORRENT_GB", "100"),
        max_total=gigabytes(env, "TORRENT_WATCH_MAX_TOTAL_GB", "500"),
        min_free=gigabytes(env, "TORRENT_WATCH_MIN_FREE_GB", "500"),
    )


def read_url(opener: urllib.request.OpenerDirector, url: str, body: bytes | None, what: str) -> str:
    """Read a URL as text, with POST if there's a body; anything but a success raises, naming ``what`` failed."""
    # load_config checks the index's address is https, and qBittorrent's is this stack's own.
    request = urllib.request.Request(url, body, {"User-Agent": "torrent-watch"})  # noqa: S310
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        err.close()  # it holds the response open until it is closed
        message = f"can't read {what}: {err}"
        raise WatchError(message) from err
    except (OSError, http.client.HTTPException) as err:
        message = f"can't read {what}: {err}"
        raise WatchError(message) from err


def fetch(url: str) -> str:
    """Get a page of the index as text."""
    return read_url(urllib.request.build_opener(), url, None, "the index")


def parse_search(text: str) -> list[Row]:
    """Read a search's results; a page that is neither results nor "no match" is an error, never an empty list."""
    rows = []
    for line in text.splitlines():
        row = ROW_RE.match(line)
        magnet = MAGNET_RE.match(row["magnet"]) if row else None
        if row and magnet:
            rows.append(Row(row["repo"], magnet["hash"].lower(), row["magnet"]))
        elif line.strip() and "magnet:" in line:
            log.warning("ignored a search result this can't read: %s", line.split("magnet:")[0].strip())
    if not rows and not text.lstrip().startswith(NO_MATCH):
        message = "the index's search answered with something this can't read"
        raise WatchError(message)
    return rows


class Index:
    """The model index: its plain-text search and its list of models."""

    def __init__(self, base_url: str, get: Callable[[str], str] = fetch) -> None:
        """Remember where the index is, and how to read a page."""
        self.base_url = base_url
        self.get = get

    def search(self, term: Term) -> list[Row]:
        """Return the torrents a term matches."""
        words = "+".join(urllib.parse.quote(word, safe="") for word in term.words)
        rows = parse_search(self.get(f"{self.base_url}/s/{words}"))
        if len(rows) >= BROAD_TERM_ROWS:
            log.warning("%r matches %d torrents: new ones may not show up, so narrow it", term.text, len(rows))
        if term.author:
            return [row for row in rows if row.repo.split("/")[0].lower() == term.author]
        return rows

    def details(self, repo: str) -> Details:
        """Return a model's licence and size, each empty if the index doesn't give it."""
        try:
            models = json.loads(self.get(f"{self.base_url}/api/models?{urllib.parse.urlencode({'q': repo})}"))["models"]
            model = next((model for model in models if model.get("id") == repo), {})
        except (ValueError, KeyError, TypeError, AttributeError) as err:
            log.warning("can't read the details of %s: %r", repo, err)
            return Details(None, None)
        license_name, size = model.get("license"), model.get("sizeBytes")
        return Details(
            license_name.lower() if isinstance(license_name, str) else None,
            size if type(size) is int and size > 0 else None,
        )


class Qbittorrent:
    """The parts of qBittorrent's web API this needs."""

    def __init__(self, url: str, username: str, password_file: Path) -> None:
        """Remember how to reach qBittorrent; each poll signs in again."""
        self.url = url
        self.username = username
        self.password_file = password_file
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self, path: str, data: dict[str, str] | None = None) -> str:
        """Call the API, with POST if there's data; anything but a success raises."""
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        return read_url(self.opener, f"{self.url}/api/v2/{path}", body, f"qBittorrent's {path}")

    def login(self) -> None:
        """Sign in; qBittorrent answers a wrong password with 401, or (before 5.2) with no session cookie."""
        try:
            password = self.password_file.read_text(encoding="utf-8").strip()
        except OSError as err:
            message = f"can't read qBittorrent's password: {err}"
            raise WatchError(message) from err
        if not password:
            message = f"qBittorrent's password file {self.password_file} is empty"
            raise WatchError(message)
        self.cookies.clear()
        refused = "qBittorrent refused the sign-in: check the password file and the username"
        try:
            self.call("auth/login", {"username": self.username, "password": password})
        except WatchError as err:
            cause = err.__cause__
            if isinstance(cause, urllib.error.HTTPError) and cause.code == HTTPStatus.UNAUTHORIZED:
                raise WatchError(refused) from err
            raise
        if not len(self.cookies):
            raise WatchError(refused)

    def maindata(self) -> dict[str, Any]:
        """Return qBittorrent's whole state: every torrent, and the free space."""
        return json.loads(self.call("sync/maindata?rid=0"))

    def add(self, magnet: str) -> None:
        """Add a magnet link under this script's category and folder."""
        answer = self.call(
            "torrents/add",
            {"urls": magnet, "category": CATEGORY, "savepath": SAVE_PATH, "autoTMM": "false"},
        ).strip()
        if answer != "Ok." and '"failure_count":0' not in answer.replace(" ", ""):
            message = f"qBittorrent didn't take the magnet link: {answer[:200]}"
            raise WatchError(message)

    def stop(self, hashes: tuple[str, ...]) -> None:
        """Stop these torrents."""
        self.call("torrents/stop", {"hashes": "|".join(hashes)})

    def force_seeding(self, hashes: tuple[str, ...]) -> None:
        """Switch these torrents to forced seeding."""
        self.call("torrents/setForceStart", {"hashes": "|".join(hashes), "value": "true"})


def read_snapshot(maindata: dict[str, Any], done: dict[str, dict[str, Any]]) -> Snapshot:
    """Sum up qBittorrent's state; a torrent with no metadata yet counts at the size the index listed for it."""
    free = maindata.get("server_state", {}).get("free_space_on_disk")
    if type(free) is not int or free <= 0:
        message = f"qBittorrent gave no free space (it can take a moment after it starts): {free!r}"
        raise WatchError(message)
    known: set[str] = set()
    total = left = 0
    incomplete = []
    seeding = []
    for key, torrent in maindata.get("torrents", {}).items():
        infohash = torrent.get("infohash_v1") or key
        known |= {key, infohash}
        size, remaining = torrent.get("total_size", 0), torrent.get("amount_left", 0)
        if size <= 0:
            size = remaining = done.get(infohash, {}).get("size", 0)
        left += remaining
        if torrent.get("category") == CATEGORY:
            total += size
            if remaining > 0 and not torrent.get("state", "").startswith(("stopped", "paused")):
                incomplete.append(key)
        if torrent.get("state") in SEEDING_STATES:
            seeding.append(key)
    return Snapshot(free, frozenset(known), total, left, tuple(incomplete), tuple(seeding))


def refusal(details: Details, config: Config, snapshot: Snapshot) -> str | None:
    """Return why a torrent can't be added now, or None if it can."""
    if details.license not in ALLOWED_LICENSES:
        return f"its licence is {details.license or 'unknown'}"
    if details.size is None:
        return "its size is unknown"
    if details.size > config.max_torrent:
        return f"{details.size / GB:.1f} GB is over the limit per torrent"
    if snapshot.total + details.size > config.max_total:
        return f"{details.size / GB:.1f} GB would go over the limit for all of them"
    if snapshot.free - snapshot.left - details.size < config.min_free:
        return f"{details.size / GB:.1f} GB would leave too little free space"
    return None


def load_state(path: Path) -> dict[str, Any]:
    """Read what earlier polls recorded; a damaged file starts again, which only records and adds nothing."""
    state: Any = {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as err:
        log.error("starting again from an empty state, because %s can't be read: %s", path, err)  # noqa: TRY400 # no traceback
    if not isinstance(state, dict):
        state = {}
    return {
        "terms": state.get("terms", []),
        "done": state.get("done", {}),
        "pending": state.get("pending", {}),
        "forced": state.get("forced", []),
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Replace the state file in one step, so that a crash leaves the old one or the new one."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


class Watch:
    """One poll: what it works with, and how many torrents it has added so far."""

    def __init__(self, config: Config, index: Index, client: Qbittorrent, state: dict[str, Any]) -> None:
        """Keep what the poll needs."""
        self.config = config
        self.index = index
        self.client = client
        self.state = state
        self.added = 0

    def record_existing(self, term: Term, rows: list[Row]) -> None:
        """Run a term for the first time: note what it matches, so that only what comes later is added."""
        for row in rows:
            if row.infohash not in self.state["pending"]:
                self.state["done"].setdefault(row.infohash, {"repo": row.repo, "why": "listed before"})
        self.state["terms"].append(term.text)
        log.info("%r is new: it matches %d torrents already, which stay out", term.text, len(rows))

    def details_of(self, row: Row) -> Details:
        """Return a torrent's details, from an earlier poll if that one got them all."""
        known = self.state["pending"].get(row.infohash, {})
        if known.get("license") and known.get("size"):
            return Details(known["license"], known["size"])
        return self.index.details(row.repo)

    def consider(self, row: Row, snapshot: Snapshot) -> Snapshot:
        """Add one new torrent if it may be added, and return the snapshot with it counted."""
        try:
            details = self.details_of(row)
        except WatchError as err:
            log.warning("no details for %s yet: %s", row.repo, err)
            return snapshot
        pending = self.state["pending"]
        reason = refusal(details, self.config, snapshot)
        if reason:
            if pending.get(row.infohash, {}).get("reason") != reason:
                log.info("not adding %s: %s", row.repo, reason)
            pending[row.infohash] = {
                "repo": row.repo,
                "license": details.license,
                "size": details.size,
                "reason": reason,
            }
            return snapshot
        size = details.size or 0
        self.client.add(row.magnet)
        self.added += 1
        pending.pop(row.infohash, None)
        self.state["done"][row.infohash] = {"repo": row.repo, "why": "added", "size": size}
        log.info("added %s (%s, %.1f GB)", row.repo, details.license, size / GB)
        return snapshot._replace(total=snapshot.total + size, left=snapshot.left + size)

    def run_term(self, term: Term, snapshot: Snapshot) -> Snapshot:
        """Look at what one term matches."""
        rows = self.index.search(term)
        if term.text not in self.state["terms"]:
            self.record_existing(term, rows)
            return snapshot
        done = self.state["done"]
        for row in rows:
            if row.infohash in done:
                continue
            if row.infohash in snapshot.known:
                self.state["pending"].pop(row.infohash, None)
                done[row.infohash] = {"repo": row.repo, "why": "already in qBittorrent"}
            elif self.added < MAX_ADDS_PER_POLL:
                snapshot = self.consider(row, snapshot)
        return snapshot

    def stop_if_short_of_space(self, snapshot: Snapshot) -> None:
        """Stop this script's own downloads if the share is below the free space asked for."""
        if snapshot.free < self.config.min_free and snapshot.incomplete:
            self.client.stop(snapshot.incomplete)
            log.warning(
                "only %.1f GB free: stopped %d of its own unfinished torrents, until someone starts them again",
                snapshot.free / GB,
                len(snapshot.incomplete),
            )

    def force_finished(self, snapshot: Snapshot) -> None:
        """Switch every finished torrent to forced seeding, once each; failing to doesn't hold up the poll."""
        forced = self.state["forced"]
        finished = tuple(key for key in snapshot.seeding if key not in forced)
        if not finished:
            return
        try:
            self.client.force_seeding(finished)
        except WatchError as err:
            log.warning("can't switch %d finished torrents to forced seeding yet: %s", len(finished), err)
            return
        forced.extend(finished)
        log.info("switched %d finished torrents to forced seeding", len(finished))

    def run(self) -> None:
        """Run one poll."""
        self.client.login()
        snapshot = read_snapshot(self.client.maindata(), self.state["done"])
        self.stop_if_short_of_space(snapshot)
        self.force_finished(snapshot)
        for position, term in enumerate(self.config.terms):
            if position:
                time.sleep(self.config.pause_seconds)
            try:
                snapshot = self.run_term(term, snapshot)
            except WatchError as err:
                log.warning("skipping %r for now: %s", term.text, err)
            save_state(self.config.state_file, self.state)


def poll(env: Mapping[str, str]) -> None:
    """Run one poll with the settings in ``env``. It never raises: the next poll tries again."""
    HEARTBEAT.touch()
    try:
        config = load_config(env)
        if not config.terms:
            log.info("no watch terms set, so nothing to do")
            return
        client = Qbittorrent(config.qbittorrent_url, config.username, config.password_file)
        Watch(config, Index(config.index_url), client, load_state(config.state_file)).run()
    except WatchError as err:
        log.error("%s", err)  # noqa: TRY400 # the message says it all
    except Exception:
        log.exception("this poll failed, and the next one tries again")


def healthy() -> bool:
    """Whether a poll has started recently; the index being down doesn't make this service unhealthy."""
    try:
        return time.time() - HEARTBEAT.stat().st_mtime < 2 * POLL_SECONDS
    except OSError:
        return False


def stop_now(_signal: int, _frame: FrameType | None) -> None:
    """Leave at once when Docker asks: the state file is only ever replaced whole."""
    sys.exit(0)


def main(argv: list[str] | None = None) -> int:
    """Poll for ever, or once, or report whether the poller is alive; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--once", action="store_true", help="run one poll and exit")
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 if a poll has started recently")
    args = parser.parse_args(argv)
    if args.healthcheck:
        return 0 if healthy() else 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    signal.signal(signal.SIGTERM, stop_now)
    poll(os.environ)
    while not args.once:
        time.sleep(POLL_SECONDS)
        poll(os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main())
