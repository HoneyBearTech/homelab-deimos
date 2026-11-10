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

With ``TORRENT_WATCH_AT_RISK`` it also adds what the index counts as at risk: a torrent with no observed seeders whose
source is still available, smallest first, under the same rules. Nothing is recorded first for these, since what is at
risk now is the point. The index's list has ended at different lengths from one run to the next, so what it sees may not
be everything at risk. Each is added from the index's own .torrent file, which carries its metadata, after checking it
is the torrent the listing names. A torrent at risk with no .torrent file on the index is left out: with no seeders, no
peer could give it its metadata either.

For a torrent at risk with no .torrent file, it builds a new torrent itself, one model at a time: it downloads the files
the listing names from the model's source at the revision the listing pins, checks each against the listing's SHA-256,
has qBittorrent make a hybrid (v1 and v2) torrent of them with the listing's trackers and web seed, and seeds it. The
.torrent file, with a sheet of what the index's submit form asks, goes in ready-to-submit for someone to submit.

One of its own torrents that stays just short of done for 6 hours (the index's web seed stalls plain v1 torrents with
several files at 99 %) has its unfinished files fetched from the source the same way, checked, and put in place, and
qBittorrent checks the torrent again. Each torrent gets one such repair.

One of its own torrents that has found no metadata in a day is removed. Nothing of it was downloaded, so nothing is
lost, and the room it took under the limits is free again.

A model whose name marks it as uncensored is never added, however it was found. That goes by the name alone, with the
words the index's own filter looks for, so a model with its safeguards removed under another name gets through.

Once any torrent in qBittorrent has finished, not only one of its own, the next poll switches it to forced seeding, so
that qBittorrent's queue limits don't stop it seeding. That happens once per torrent: one switched back by hand, or
stopped, stays that way.

Standard library only: compose.yaml runs it in a pinned Python image.
"""

from __future__ import annotations

import argparse
import hashlib
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
import uuid
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
# How long one of its torrents may go without metadata before it is removed: no peer has it, and nothing was downloaded.
GIVE_UP_SECONDS = 24 * 3600
# Where it downloads a model from to build or repair a torrent: its source, at the revision its listing pins.
SOURCE_URL = "https://huggingface.co"
# The folder, inside its own, it leaves torrents it built in for someone to submit to the index.
READY_FOLDER = "ready-to-submit"
# How long one poll may spend downloading, so that polls keep coming; a download carries on at the next.
WORK_SECONDS = 20 * 60
# How long one of its torrents may sit just short of done before its unfinished files are fetched from the source.
STALL_SECONDS = 6 * 3600
STALL_PROGRESS = 0.99
CHUNK_BYTES = 1024 * 1024
# What a model's outcome is while it waits for a torrent built here.
NEEDS_TORRENT = "no .torrent on the index"
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRACKER_RE = re.compile(r"^(?:udp|https?)://[\w.-]+(?::\d+)?/\S*$")
# The index serves each torrent's .torrent file here.
TORRENT_PATH_RE = re.compile(r"^/api/torrents/(?P<hash>[0-9a-f]{40})\.torrent$")
# What a model's last outcome may be for the list of torrents at risk to look at it again.
RETRY = frozenset(
    {"listed before", "gave up: no metadata", "no .torrent on the index", "check again", "rebuild failed"}
)
# The options every torrent it adds goes in with.
ADD_OPTIONS = {"category": CATEGORY, "savepath": SAVE_PATH, "autoTMM": "false"}
HEARTBEAT = Path("/tmp/torrent-watch-heartbeat")  # noqa: S108 # the container's own tmpfs
# A second limit on top of the sizes, in case the index ever lists a run of torrents as new by mistake.
MAX_ADDS_PER_POLL = 3
# How often it asks the index for the whole list, about 100 requests so far; each poll adds from the last answer.
AT_RISK_RESCAN_SECONDS = 6 * 3600
# The longest list so far took 42 pages for one licence, and lengths vary: this only guards against one that never ends.
MAX_PAGES_PER_LICENSE = 100
# The search stops at about 90 rows, best-seeded first, so a new torrent with no seeders can miss a longer list.
BROAD_TERM_ROWS = 75
TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
GB = 1000**3  # as the index counts them
ROW_RE = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)\s.*\s(?P<magnet>magnet:\?\S+)\s*$")
MAGNET_RE = re.compile(r"^magnet:\?xt=urn:btih:(?P<hash>[0-9a-fA-F]{40})(?:&\S*)?$")
NO_MATCH = "No torrents match"
# What the index's own filter for uncensored models looks for: these words anywhere in the name, in any case.
UNCENSORED_WORDS = ("abliterated", "decensored", "heretic", "obliterated", "obliteratus", "uncensored")


class WatchError(Exception):
    """Something this poll can't go on without; it is logged, and the next poll tries again."""


class JobError(WatchError):
    """Something that means a rebuild or repair can't work, so it is given up rather than tried again."""


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
    torrent_path: str | None = None


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
    at_risk: bool = False
    pause_seconds: float = 1.0
    source_url: str = SOURCE_URL
    files_dir: Path = Path(SAVE_PATH)
    work_seconds: float = WORK_SECONDS


class Snapshot(NamedTuple):
    """What qBittorrent holds right now, in bytes."""

    free: int
    known: frozenset[str]
    total: int
    left: int
    incomplete: tuple[str, ...]
    seeding: tuple[str, ...]
    no_metadata: tuple[str, ...] = ()
    near_done: tuple[tuple[str, str, int], ...] = ()


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
    at_risk = env.get("TORRENT_WATCH_AT_RISK") or "false"
    if at_risk not in {"true", "false"}:
        message = f"TORRENT_WATCH_AT_RISK must be true or false, not {at_risk!r}"
        raise WatchError(message)
    if (terms or at_risk == "true") and not index_url.startswith("https://"):
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
        at_risk=at_risk == "true",
    )


def read_bytes(
    opener: urllib.request.OpenerDirector, url: str, body: bytes | None, what: str, content_type: str | None = None
) -> bytes:
    """Read a URL, with POST if there's a body; anything but a success raises, naming ``what`` failed."""
    headers = {"User-Agent": "torrent-watch", **({"Content-Type": content_type} if content_type else {})}
    # load_config checks the index's address is https, and qBittorrent's is this stack's own.
    request = urllib.request.Request(url, body, headers)  # noqa: S310
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as err:
        err.close()  # it holds the response open until it is closed
        message = f"can't read {what}: {err}"
        raise WatchError(message) from err
    except (OSError, http.client.HTTPException) as err:
        message = f"can't read {what}: {err}"
        raise WatchError(message) from err


def read_url(opener: urllib.request.OpenerDirector, url: str, body: bytes | None, what: str) -> str:
    """Read a URL as text, with POST if there's a body."""
    return read_bytes(opener, url, body, what).decode("utf-8", "replace")


def fetch(url: str) -> str:
    """Get a page of the index as text."""
    return read_url(urllib.request.build_opener(), url, None, "the index")


def fetch_bytes(url: str) -> bytes:
    """Get a file from the index."""
    return read_bytes(urllib.request.build_opener(), url, None, "the index")


def bdecode(data: bytes, start: int = 0) -> tuple[Any, int]:
    """Read the bencoded value at ``start``; return it and where it ends. Anything malformed raises ValueError."""
    kind = data[start : start + 1]
    if kind == b"i":
        end = data.index(b"e", start)
        return int(data[start + 1 : end]), end + 1
    if kind in {b"l", b"d"}:
        items, position = [], start + 1
        while data[position : position + 1] != b"e":
            item, position = bdecode(data, position)
            items.append(item)
        return (items if kind == b"l" else dict(zip(items[::2], items[1::2], strict=True))), position + 1
    colon = data.index(b":", start)
    end = colon + 1 + int(data[start:colon])
    if end > len(data):
        message = "a string runs past the end"
        raise ValueError(message)
    return data[colon + 1 : end], end


def info_hash(torrent: bytes) -> str:
    """Return a .torrent file's v1 infohash: the SHA-1 of its info dictionary as written. Anything else raises."""
    if torrent[:1] != b"d":
        message = "not a dictionary"
        raise ValueError(message)
    position = 1
    while torrent[position : position + 1] != b"e":
        key, position = bdecode(torrent, position)
        start = position
        _, position = bdecode(torrent, position)
        if key == b"info":
            return hashlib.sha1(torrent[start:position]).hexdigest()  # noqa: S324 # BitTorrent's v1 infohash is SHA-1
    message = "no info dictionary"
    raise ValueError(message)


def multipart(fields: dict[str, str], name: str, filename: str, content: bytes) -> tuple[bytes, str]:
    """Encode a form with one file as multipart/form-data; return the body and its content type."""
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        for key, value in fields.items()
    ]
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
    parts.append(f"{head}Content-Type: application/x-bittorrent\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def safe_relative(path: object) -> str:
    """Return a file's path inside a model if it stays inside it; anything else raises ValueError."""
    if not isinstance(path, str) or not path:
        message = f"not a path: {path!r}"
        raise ValueError(message)
    if any(part in {"", ".", ".."} or "\\" in part or "\x00" in part for part in path.split("/")):
        message = f"a path that leaves its folder: {path!r}"
        raise ValueError(message)
    return path


def sha256_of(path: Path) -> str:
    """Return a file's SHA-256."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, sha256: str, deadline: float, limit: int) -> bool:
    """Download url to target through a .part file that a later poll resumes; True once it is complete and checked.

    False when the deadline comes first. A file over limit bytes, or one that doesn't match sha256, raises JobError.
    """
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "torrent-watch", **({"Range": f"bytes={have}-"} if have else {})}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 # the source's address, from the settings
    try:
        with urllib.request.build_opener().open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != HTTPStatus.PARTIAL_CONTENT:
                have = 0
            with part.open("ab" if have else "wb") as out:
                while chunk := response.read(CHUNK_BYTES):
                    have += len(chunk)
                    if have > limit:
                        message = f"{target.name} from the source is larger than the whole model"
                        raise JobError(message)
                    out.write(chunk)
                    HEARTBEAT.touch()
                    if time.time() >= deadline:
                        return False
    except urllib.error.HTTPError as err:
        err.close()
        if err.code != HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
            message = f"can't download {target.name} from the source: {err}"
            raise WatchError(message) from err
    except (OSError, http.client.HTTPException) as err:
        message = f"can't download {target.name} from the source: {err}"
        raise WatchError(message) from err
    if sha256_of(part) != sha256:
        part.unlink()
        message = f"{target.name} from the source doesn't match its listing's checksum"
        raise JobError(message)
    part.replace(target)
    return True


def magnet_parts(magnet: str, index_url: str) -> tuple[list[str], str | None]:
    """Return a listing's magnet link's trackers, and its web seed if that is on the index itself."""
    query = urllib.parse.parse_qs(magnet.partition("?")[2])
    trackers = [tracker for tracker in query.get("tr", []) if TRACKER_RE.match(tracker)]
    seeds = [seed for seed in query.get("ws", []) if seed.startswith(f"{index_url}/api/ws/")]
    return trackers, (seeds[0] if seeds else None)


def check_added(answer: str) -> None:
    """Raise unless qBittorrent's answer to an add says it took the torrent."""
    answer = answer.strip()
    if answer != "Ok." and '"failure_count":0' not in answer.replace(" ", ""):
        message = f"qBittorrent didn't take the torrent: {answer[:200]}"
        raise WatchError(message)


def is_uncensored(repo: str) -> bool:
    """Whether a model's name marks it as uncensored."""
    name = repo.lower()
    return any(word in name for word in UNCENSORED_WORDS)


def is_at_risk(model: dict[str, Any]) -> bool:
    """Whether the index counts a model as at risk: a torrent, no observed seeders, its source still available."""
    seed = model.get("seed")
    return model.get("seeded") is True and type(seed) is int and seed == 0 and model.get("hfBacked") is True


def read_details(model: dict[str, Any]) -> Details:
    """Read a model's licence and size, each empty if the index doesn't give it."""
    license_name, size = model.get("license"), model.get("sizeBytes")
    return Details(
        license_name.lower() if isinstance(license_name, str) else None,
        size if type(size) is int and size > 0 else None,
    )


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

    def __init__(
        self, base_url: str, get: Callable[[str], str] = fetch, get_bytes: Callable[[str], bytes] = fetch_bytes
    ) -> None:
        """Remember where the index is, and how to read a page or a file."""
        self.base_url = base_url
        self.get = get
        self.get_bytes = get_bytes

    def search(self, term: Term) -> list[Row]:
        """Return the torrents a term matches."""
        words = "+".join(urllib.parse.quote(word, safe="") for word in term.words)
        rows = parse_search(self.get(f"{self.base_url}/s/{words}"))
        if len(rows) >= BROAD_TERM_ROWS:
            log.warning("%r matches %d torrents: new ones may not show up, so narrow it", term.text, len(rows))
        rows = [row for row in rows if not is_uncensored(row.repo)]
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
        return read_details(model)

    def at_risk(self, pause_seconds: float) -> list[dict[str, Any]]:
        """List the models at risk under the allowed licences, smallest first; a list it can't read raises."""
        found = {}
        try:
            for license_name in sorted(ALLOWED_LICENSES):
                cursor = None
                for _ in range(MAX_PAGES_PER_LICENSE):
                    query = {"license": license_name, **({"cursor": cursor} if cursor else {})}
                    page = json.loads(self.get(f"{self.base_url}/api/models?{urllib.parse.urlencode(query)}"))
                    for model in filter(is_at_risk, page["models"]):
                        if not is_uncensored(model["id"]):
                            found[model["id"]] = {"repo": model["id"], **read_details(model)._asdict()}
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                    time.sleep(pause_seconds)
        except (ValueError, KeyError, TypeError, AttributeError) as err:
            message = f"can't read the index's list of models: {err!r}"
            raise WatchError(message) from err
        return sorted(found.values(), key=lambda model: model["size"] or 0)

    def at_risk_torrent(self, repo: str) -> Row | None:
        """Return a model's torrent if it is still at risk with no terms to accept, or None if it isn't."""
        try:
            answer = json.loads(self.get(f"{self.base_url}/api/torrents?{urllib.parse.urlencode({'repo': repo})}"))
            magnet, seeders, terms = answer.get("magnet"), answer.get("seeders"), answer.get("terms")
        except (ValueError, AttributeError) as err:
            message = f"can't read the torrent of {repo}: {err!r}"
            raise WatchError(message) from err
        match = MAGNET_RE.match(magnet) if isinstance(magnet, str) else None
        if match is None or terms or seeders != 0:
            return None
        infohash, path = match["hash"].lower(), answer.get("torrent")
        found = TORRENT_PATH_RE.match(path) if isinstance(path, str) else None
        return Row(repo, infohash, magnet, path if found and found["hash"] == infohash else None)

    def listing(self, repo: str) -> dict[str, Any]:
        """Return a model's listing, its revision and every file's path and SHA-256 checked; anything else raises."""
        try:
            answer = json.loads(self.get(f"{self.base_url}/api/torrents?{urllib.parse.urlencode({'repo': repo})}"))
            revision = answer["revision"]
            files = [{"path": safe_relative(file["path"]), "sha256": file["sha256"]} for file in answer["files"]]
            if not REVISION_RE.match(revision) or not files or not all(SHA256_RE.match(f["sha256"]) for f in files):
                message = "no revision, no files, or a checksum that isn't one"
                raise ValueError(message)  # noqa: TRY301 # handled with the rest just below
        except (ValueError, KeyError, TypeError, AttributeError) as err:
            message = f"the listing of {repo} isn't one to build a torrent from: {err}"
            raise JobError(message) from err
        return {**answer, "revision": revision, "files": files}

    def torrent_file(self, path: str) -> bytes:
        """Return one of the index's .torrent files, by the path its listing gives."""
        return self.get_bytes(f"{self.base_url}{path}")


class Qbittorrent:
    """The parts of qBittorrent's web API this needs."""

    def __init__(self, url: str, username: str, password_file: Path) -> None:
        """Remember how to reach qBittorrent; each poll signs in again."""
        self.url = url
        self.username = username
        self.password_file = password_file
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def read(self, path: str, body: bytes | None = None, content_type: str | None = None) -> bytes:
        """Call the API, with POST if there's a body; anything but a success raises.

        qBittorrent answers 403 once its session has ended, which a long download inside one poll can outlast, so a 403
        signs in again and tries once more.
        """
        url, what = f"{self.url}/api/v2/{path}", f"qBittorrent's {path}"
        try:
            return read_bytes(self.opener, url, body, what, content_type)
        except WatchError as err:
            cause = err.__cause__
            if path == "auth/login" or not (
                isinstance(cause, urllib.error.HTTPError) and cause.code == HTTPStatus.FORBIDDEN
            ):
                raise
        log.info("qBittorrent's session had ended, so signing in again")
        self.login()
        return read_bytes(self.opener, url, body, what, content_type)

    def call(self, path: str, data: dict[str, str] | None = None) -> str:
        """Call the API as text, with POST if there's data."""
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        return self.read(path, body).decode("utf-8", "replace")

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
        check_added(self.call("torrents/add", {"urls": magnet, **ADD_OPTIONS}))

    def add_file(self, torrent: bytes, infohash: str) -> None:
        """Add a .torrent file under this script's category and folder."""
        body, content_type = multipart(ADD_OPTIONS, "torrents", f"{infohash}.torrent", torrent)
        check_added(self.read("torrents/add", body, content_type).decode("utf-8", "replace"))

    def create_torrent(self, source: str, trackers: list[str], web_seed: str | None, comment: str) -> str:
        """Have qBittorrent make a hybrid torrent of a folder and seed it; return the task's ID."""
        form = {"sourcePath": source, "format": "hybrid", "startSeeding": "true", "comment": comment}
        form |= {"trackers": "|".join(trackers)} if trackers else {}
        form |= {"urlSeeds": web_seed} if web_seed else {}
        try:
            return json.loads(self.call("torrentcreator/addTask", form))["taskID"]
        except (ValueError, KeyError, TypeError) as err:
            message = f"qBittorrent didn't start making the torrent: {err!r}"
            raise WatchError(message) from err

    def creation(self, task: str) -> dict[str, Any]:
        """Return how making a torrent is going: its status, and an error message if it failed."""
        tasks = json.loads(self.call(f"torrentcreator/status?{urllib.parse.urlencode({'taskID': task})}"))
        return tasks[0] if tasks else {"status": "Failed", "errorMessage": "qBittorrent has no such task"}

    def created_torrent(self, task: str) -> bytes:
        """Return the .torrent file qBittorrent made, and forget the task."""
        query = urllib.parse.urlencode({"taskID": task})
        torrent = self.read(f"torrentcreator/torrentFile?{query}")
        self.call("torrentcreator/deleteTask", {"taskID": task})
        return torrent

    def set_category(self, key: str) -> None:
        """Put a torrent under this script's category."""
        self.call("torrents/setCategory", {"hashes": key, "category": CATEGORY})

    def files(self, key: str) -> list[dict[str, Any]]:
        """Return a torrent's files, with how far each has come."""
        return json.loads(self.call(f"torrents/files?{urllib.parse.urlencode({'hash': key})}"))

    def recheck(self, key: str) -> None:
        """Check a torrent's data again."""
        self.call("torrents/recheck", {"hashes": key})

    def start(self, keys: tuple[str, ...]) -> None:
        """Start these torrents."""
        self.call("torrents/start", {"hashes": "|".join(keys)})

    def states(self, keys: tuple[str, ...]) -> dict[str, str]:
        """Return these torrents' states, by key; one qBittorrent doesn't hold is missing."""
        answer = json.loads(self.call(f"torrents/info?{urllib.parse.urlencode({'hashes': '|'.join(keys)})}"))
        return {torrent["hash"]: torrent.get("state", "") for torrent in answer}

    def remove(self, hashes: tuple[str, ...]) -> None:
        """Remove these torrents from qBittorrent, leaving any files they have."""
        self.call("torrents/delete", {"hashes": "|".join(hashes), "deleteFiles": "false"})

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
    incomplete, seeding, no_metadata, near_done = [], [], [], []
    for key, torrent in maindata.get("torrents", {}).items():
        infohash = torrent.get("infohash_v1") or key
        known |= {key, infohash}
        size, remaining = torrent.get("total_size", 0), torrent.get("amount_left", 0)
        has_metadata = size > 0
        if not has_metadata:
            size = remaining = done.get(infohash, {}).get("size", 0)
        left += remaining
        if torrent.get("category") == CATEGORY:
            total += size
            if remaining > 0 and not torrent.get("state", "").startswith(("stopped", "paused")):
                incomplete.append(key)
            if not has_metadata:
                no_metadata.append(key)
            elif remaining > 0 and torrent.get("progress", 0) >= STALL_PROGRESS:
                near_done.append((key, infohash, remaining))
        if torrent.get("state") in SEEDING_STATES:
            seeding.append(key)
    lists = (tuple(incomplete), tuple(seeding), tuple(no_metadata), tuple(near_done))
    return Snapshot(free, frozenset(known), total, left, *lists)


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
        "at_risk": state.get("at_risk", {}),
        "no_metadata": state.get("no_metadata", {}),
        "near_done": state.get("near_done", {}),
        "repaired": state.get("repaired", []),
        "rebuild": state.get("rebuild"),
        "repair": state.get("repair"),
        "restart": state.get("restart", []),
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
        return self.add(row, details, snapshot)

    def add(self, row: Row, details: Details, snapshot: Snapshot, torrent: bytes | None = None) -> Snapshot:
        """Add a torrent that may be added, from its .torrent file if there is one, and return the snapshot with it."""
        size = details.size or 0
        if torrent is None:
            self.client.add(row.magnet)
        else:
            self.client.add_file(torrent, row.infohash)
        self.added += 1
        self.state["pending"].pop(row.infohash, None)
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

    def at_risk_models(self) -> list[dict[str, Any]]:
        """Return the models at risk, asking the index again once its last answer is old."""
        known = self.state["at_risk"]
        if time.time() - known.get("scanned", 0) >= AT_RISK_RESCAN_SECONDS:
            known["models"] = self.index.at_risk(self.config.pause_seconds)
            known["scanned"] = time.time()
        return known["models"]

    def take_at_risk(self, model: dict[str, Any], snapshot: Snapshot) -> Snapshot:
        """Add one model at risk if it still is, and return the snapshot with it counted."""
        row = self.index.at_risk_torrent(model["repo"])
        models, done = self.state["at_risk"]["models"], self.state["done"]
        if row is None:
            log.info("leaving out %s: it has seeders now, or terms to accept, or no torrent", model["repo"])
            models.remove(model)
            return snapshot
        if row.infohash in snapshot.known:
            done[row.infohash] = {"repo": row.repo, "why": "already in qBittorrent"}
            return snapshot
        if row.torrent_path is None:
            why = done.get(row.infohash, {}).get("why")
            if why != "rebuild failed":
                if why != NEEDS_TORRENT:
                    log.info("not adding %s: no .torrent on the index, so it waits for a torrent built here", row.repo)
                entry = {"repo": row.repo, "why": NEEDS_TORRENT, "license": model["license"], "size": model["size"]}
                done[row.infohash] = entry
            models.remove(model)
            return snapshot
        torrent = self.index.torrent_file(row.torrent_path)
        try:
            matches = info_hash(torrent) == row.infohash
        except ValueError, RecursionError:
            matches = False
        if not matches:
            log.warning("leaving out %s: its .torrent file isn't the torrent its listing names", row.repo)
            models.remove(model)
            return snapshot
        return self.add(row, Details(model["license"], model["size"]), snapshot, torrent)

    def run_at_risk(self, snapshot: Snapshot) -> None:
        """Add what is at risk, smallest first, while the limits allow it."""
        models = self.at_risk_models()
        taken = {entry["repo"] for entry in self.state["done"].values() if entry.get("why") not in RETRY}
        left_out = waiting = 0
        for model in list(models):
            if model["repo"] in taken:
                continue
            if refusal(Details(model["license"], model["size"]), self.config, snapshot):
                left_out += 1
            elif self.added >= MAX_ADDS_PER_POLL:
                waiting += 1
            else:
                snapshot = self.take_at_risk(model, snapshot)
        log.info(
            "at risk: %d listed, %d kept out by the limits, %d waiting for a later poll", len(models), left_out, waiting
        )

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

    def give_up_without_metadata(self, snapshot: Snapshot) -> None:
        """Remove its own torrents that have found no metadata in a day; nothing of them was downloaded."""
        waiting, now = self.state["no_metadata"], time.time()
        for key in set(waiting) - set(snapshot.no_metadata):
            del waiting[key]
        expired = tuple(key for key in snapshot.no_metadata if now - waiting.setdefault(key, now) >= GIVE_UP_SECONDS)
        if not expired:
            return
        try:
            self.client.remove(expired)
        except WatchError as err:
            log.warning("can't remove %d torrents that found no metadata yet: %s", len(expired), err)
            return
        for key in expired:
            del waiting[key]
            if key in self.state["done"]:
                self.state["done"][key]["why"] = "gave up: no metadata"
        log.warning(
            "removed %d of its torrents that found no metadata in a day: they held no data, and their room under the "
            "limits is free again",
            len(expired),
        )

    def source(self, repo: str, revision: str, path: str) -> str:
        """Return a file's address at the model's source, at a revision."""
        quoted = urllib.parse.quote(path, safe="/")
        return f"{self.config.source_url}/{urllib.parse.quote(repo, safe='/')}/resolve/{revision}/{quoted}"

    def count_jobs(self, snapshot: Snapshot) -> Snapshot:
        """Count a torrent being built here toward the limits, until qBittorrent holds it."""
        job = self.state["rebuild"]
        if job is None or job.get("task"):
            return snapshot
        return snapshot._replace(total=snapshot.total + job["size"], left=snapshot.left + job["size"])

    def rebuild_candidate(self, snapshot: Snapshot) -> tuple[str, dict[str, Any]] | None:
        """Return the smallest model waiting for a torrent built here that the limits allow, if any."""
        waiting = sorted(
            ((infohash, entry) for infohash, entry in self.state["done"].items() if entry.get("why") == NEEDS_TORRENT),
            key=lambda item: item[1].get("size") or 0,
        )
        for infohash, entry in waiting:
            if REPO_RE.match(entry["repo"]) and not is_uncensored(entry["repo"]):
                details = Details(entry.get("license"), entry.get("size"))
                if not refusal(details, self.config, snapshot):
                    return infohash, entry
        return None

    def start_rebuild(self, infohash: str, entry: dict[str, Any]) -> None:
        """Begin building a torrent for a model, if its listing still has none and no seeders."""
        listing = self.index.listing(entry["repo"])
        if listing.get("torrent") or listing.get("seeders") != 0:
            entry["why"] = "check again"
            return
        trackers, web_seed = magnet_parts(str(listing.get("magnet") or ""), self.index.base_url)
        name = entry["repo"].replace("/", "__")
        self.state["rebuild"] = {
            **{key: entry[key] for key in ("repo", "license", "size")},
            "name": name,
            "old": infohash,
            "revision": listing["revision"],
            "files": listing["files"],
            "verified": [],
            "trackers": trackers,
            "web_seed": web_seed,
            "magnet": listing.get("magnet"),
            "task": None,
        }
        count = len(listing["files"])
        log.info("building a torrent for %s: downloading its %d files from its source", entry["repo"], count)

    def continue_rebuild(self, deadline: float) -> None:
        """Download, then have the torrent made, then leave it ready to submit; a step carries on at the next poll."""
        job = self.state["rebuild"]
        if job["task"] is None:
            folder = self.config.files_dir / job["name"]
            limit = int(job["size"] * 1.05) + 10 * 1024 * 1024
            for file in job["files"]:
                if file["path"] in job["verified"]:
                    continue
                target = folder.joinpath(*file["path"].split("/"))
                url = self.source(job["repo"], job["revision"], file["path"])
                if not download(url, target, file["sha256"], deadline, limit):
                    return
                job["verified"].append(file["path"])
            comment = f"{job['repo']} at {job['revision']}, every file checked against its source"
            job["task"] = self.client.create_torrent(
                f"{SAVE_PATH}/{job['name']}", job["trackers"], job["web_seed"], comment
            )
            return
        creation = self.client.creation(job["task"])
        if creation.get("status") == "Failed":
            message = f"qBittorrent couldn't make the torrent: {creation.get('errorMessage')}"
            raise JobError(message)
        if creation.get("status") == "Finished":
            self.finish_rebuild(job)

    def finish_rebuild(self, job: dict[str, Any]) -> None:
        """Leave a torrent built here ready to submit, and put its seeding copy under this script's category."""
        torrent = self.client.created_torrent(job["task"])
        try:
            infohash = info_hash(torrent)
        except (ValueError, RecursionError) as err:
            message = f"qBittorrent's torrent file can't be read: {err}"
            raise JobError(message) from err
        torrents = self.client.maindata().get("torrents", {})
        key = next((key for key, value in torrents.items() if value.get("infohash_v1") == infohash), None)
        if key:
            self.client.set_category(key)
        ready = self.config.files_dir / READY_FOLDER
        ready.mkdir(parents=True, exist_ok=True)
        (ready / f"{job['name']}.torrent").write_bytes(torrent)
        trackers = "".join(f"&tr={urllib.parse.quote(tracker, safe='')}" for tracker in job["trackers"])
        sheet = [
            f"Model:     {job['repo']}",
            f"Source:    {self.config.source_url}/{job['repo']}",
            f"Revision:  {job['revision']}",
            f"Licence:   {job['license']}",
            f"Infohash:  {infohash} (v1; the torrent is a v1 and v2 hybrid)",
            f"Magnet:    magnet:?xt=urn:btih:{infohash}&dn={urllib.parse.quote(job['name'])}{trackers}",
            f"Files:     {len(job['files'])}, each checked against the listing's SHA-256 before the torrent was made",
            f"Submit:    {self.index.base_url}/submit, 'Submit a torrent', with {job['name']}.torrent",
            "",
            *(f"{file['sha256']}  {file['path']}" for file in job["files"]),
        ]
        (ready / f"{job['name']}.txt").write_text("\n".join(sheet) + "\n", encoding="utf-8")
        self.state["done"][infohash] = {"repo": job["repo"], "why": "added", "size": job["size"], "built": True}
        self.state["done"][job["old"]] = {"repo": job["repo"], "why": "built here"}
        self.state["rebuild"] = None
        seeding = "seeding" if key else "made, but qBittorrent isn't seeding it"
        log.info("built a torrent for %s (%s): ready to submit, in %s", job["repo"], seeding, ready / job["name"])

    def stalled_near_done(self, snapshot: Snapshot) -> tuple[str, str] | None:
        """Return one of its torrents that has sat just short of done for long enough to repair, if any."""
        tracked, now = self.state["near_done"], time.time()
        current = {key: (infohash, left) for key, infohash, left in snapshot.near_done}
        for key in set(tracked) - set(current):
            del tracked[key]
        found = None
        for key, (infohash, left) in current.items():
            entry = tracked.get(key)
            if entry is None or entry["left"] != left:
                tracked[key] = {"left": left, "since": now}
            elif now - entry["since"] >= STALL_SECONDS and key not in self.state["repaired"] and found is None:
                found = key, infohash
        return found

    def start_repair(self, key: str, infohash: str) -> None:
        """Begin fetching a stalled torrent's unfinished files from its source, if its listing names them all."""
        repaired = self.state["repaired"]
        repo = self.state["done"].get(infohash, {}).get("repo")
        if not isinstance(repo, str) or not REPO_RE.match(repo):
            log.warning("can't repair %s: it isn't one of the torrents this added", key)
            repaired.append(key)
            return
        try:
            listing = self.index.listing(repo)
        except JobError as err:
            log.warning("can't repair %s: %s", repo, err)
            repaired.append(key)
            return
        repaired.append(key)
        if listing.get("infohash") != infohash:
            log.warning("can't repair %s: its listing names another torrent now", repo)
            return
        checksums = {file["path"]: file["sha256"] for file in listing["files"]}
        unfinished = []
        for file in self.client.files(key):
            if file.get("progress", 1) >= 1 or "/.pad/" in f"/{file.get('name', '')}":
                continue
            try:
                local = safe_relative(file.get("name"))
            except ValueError as err:
                log.warning("can't repair %s: %s", repo, err)
                return
            path = local.split("/", 1)[1] if "/" in local else local
            if path not in checksums:
                log.warning("can't repair %s: its listing has no checksum for %s", repo, path)
                return
            unfinished.append({"path": path, "local": local, "sha256": checksums[path], "size": file.get("size", 0)})
        self.client.stop((key,))
        self.state["repair"] = {"key": key, "repo": repo, "revision": listing["revision"], "files": unfinished}
        log.info("repairing %s: fetching its %d unfinished files from its source", repo, len(unfinished))

    def continue_repair(self, deadline: float) -> None:
        """Fetch the next unfinished file; once all are in place, have qBittorrent check the torrent and start it."""
        job = self.state["repair"]
        while job["files"]:
            file = job["files"][0]
            target = self.config.files_dir.joinpath(*file["local"].split("/"))
            url = self.source(job["repo"], job["revision"], file["path"])
            if not download(url, target, file["sha256"], deadline, file["size"]):
                return
            job["files"].pop(0)
        self.client.recheck(job["key"])
        self.state["restart"].append(job["key"])
        self.state["repair"] = None
        log.info("repaired %s: every unfinished file is in place, and qBittorrent is checking it", job["repo"])

    def step(self, kind: str, work: Callable[[float], None], deadline: float) -> None:
        """Take a rebuild or repair one poll further; give it up if it can't work."""
        try:
            work(deadline)
        except JobError as err:
            job = self.state[kind]
            log.warning("giving up the %s of %s: %s", kind, job["repo"], err)
            if kind == "rebuild":
                self.state["done"][job["old"]] = {"repo": job["repo"], "why": "rebuild failed", "error": str(err)}
            self.state[kind] = None

    def restart_repaired(self) -> None:
        """Start a repaired torrent once qBittorrent has checked it: a start sent while it checks is lost."""
        keys = self.state["restart"]
        if not keys:
            return
        states = self.client.states(tuple(keys))
        for key in list(keys):
            state = states.get(key, "")
            if state.startswith(("stopped", "paused")):
                self.client.start((key,))
            elif not state.startswith("checking"):
                keys.remove(key)

    def run_jobs(self, snapshot: Snapshot) -> None:
        """Repair a stalled torrent, and build a torrent for a model with none, a poll's worth at a time."""
        deadline = time.time() + self.config.work_seconds
        self.restart_repaired()
        if self.state["repair"] is None and (stalled := self.stalled_near_done(snapshot)):
            self.start_repair(*stalled)
        if self.state["repair"]:
            self.step("repair", self.continue_repair, deadline)
        if self.state["rebuild"] is None and self.config.at_risk and (waiting := self.rebuild_candidate(snapshot)):
            try:
                self.start_rebuild(*waiting)
            except JobError as err:
                log.warning("not building a torrent for %s: %s", waiting[1]["repo"], err)
                waiting[1].update({"why": "rebuild failed", "error": str(err)})
        if self.state["rebuild"]:
            self.step("rebuild", self.continue_rebuild, deadline)

    def run(self) -> None:
        """Run one poll."""
        self.client.login()
        snapshot = self.count_jobs(read_snapshot(self.client.maindata(), self.state["done"]))
        self.stop_if_short_of_space(snapshot)
        self.force_finished(snapshot)
        self.give_up_without_metadata(snapshot)
        for position, term in enumerate(self.config.terms):
            if position:
                time.sleep(self.config.pause_seconds)
            try:
                snapshot = self.run_term(term, snapshot)
            except WatchError as err:
                log.warning("skipping %r for now: %s", term.text, err)
            save_state(self.config.state_file, self.state)
        if self.config.at_risk:
            try:
                self.run_at_risk(snapshot)
            except WatchError as err:
                log.warning("skipping the torrents at risk for now: %s", err)
        save_state(self.config.state_file, self.state)
        try:
            self.run_jobs(snapshot)
        except WatchError as err:
            log.warning("the rebuild or repair carries on at the next poll: %s", err)
        save_state(self.config.state_file, self.state)


def poll(env: Mapping[str, str]) -> None:
    """Run one poll with the settings in ``env``. It never raises: the next poll tries again."""
    HEARTBEAT.touch()
    try:
        config = load_config(env)
        if not config.terms and not config.at_risk:
            log.info("no watch terms set and at-risk torrents off, so nothing to do")
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
