#!/usr/bin/env python
"""
Build a small, reference-complete, production-like sample of the Open Library data
dumps, for integration-testing the ``solr_builder`` pipeline (see ``Jenkinsfile``).

The full dumps are ~18GB compressed / ~119M records, which makes them impractical as
a test fixture. This script streams the *per-type* dumps
(https://openlibrary.org/developers/dumps) and writes out a scaled-down copy of the
whole dump set, laid out exactly like the archive.org item the Jenkinsfile downloads
from, so ``DUMP_DIR`` can simply be pointed at the output directory.

Two properties are maintained:

1. **Realistic proportions.** The type mix, the share of editions with an ``ocaid``,
   the share of works with a reading log / with ratings, and the share of orphaned
   editions all track production. These come out of the sampling scheme rather than
   being enforced afterwards: a record's inclusion is decided by a hash of a key,
   which is independent of every field whose distribution we care about. See
   ``PROD_STATS`` for the measured production figures.

2. **No dangling references.** A key mentioned by an included document is guaranteed
   to resolve to an included document. This matters more than it looks:
   ``WorkSolrUpdater`` dereferences author and series keys via
   ``DataProvider.get_document``, whose local-postgres implementation raises on a
   missing row -- and the caller swallows it with a bare ``except``, so one dangling
   author ref silently drops the whole work from the index.

Sampling scheme (``--anchor author``, the default)::

    author picked (hash)  ->  all of that author's works  ->  all of those works'
    editions  ->  every author/series/language/cover those records reference

Anchoring on authors rather than works is deliberate. Authors are shared -- production
has 0.37 authors per work because an author averages ~2.5 works. Sampling works
independently destroys that sharing (each sampled work needs its own ~1.07 authors),
which inflates authors to roughly 31% of the sample against a production 12.9%.
Anchoring on authors keeps whole bibliographies together and brings that down to
~15.2%, measured on a 1M-record run. Closing the last of the gap would mean sampling
whole connected components of the co-authorship graph, which is not worth a union-find
over 44M edges for a test fixture. Pass ``--anchor work`` to compare.

Usage::

    # ~1M records, streamed from the latest dump on archive.org
    python scripts/solr_builder/make_sample_dumps.py 1000000 --out-dir /storage/ol-sample

    # from dumps already on disk
    python scripts/solr_builder/make_sample_dumps.py 1000000 --source /storage/openlibrary
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("openlibrary.make-sample-dumps")

# The per-type dumps, in the order the sampler must visit them. Order is load-bearing:
# editions need the selected works, authors need the refs collected from works and
# editions, and redirects/deletes/lists need every selected key.
DUMP_TYPES = ("works", "editions", "authors", "other", "redirects", "deletes", "lists")
# Aux dumps, keyed by the name used in the dump filename.
AUX_DUMPS = ("ratings", "reading-log", "covers_metadata")

LATEST_DUMP_URL = "https://openlibrary.org/data/ol_dump_latest.txt.gz"


@dataclass(frozen=True)
class ProdStats:
    """
    Production figures the sampler calibrates against.

    Counts are exact, from a full pass over each per-type dump of the
    ``ol_dump_2026-08-31`` archive.org item on 2026-09-10. The rates below them are
    estimates from a 262MB prefix of each dump (a prefix spans several complete sweeps
    of the key space, so it samples the whole range rather than one slice of it).

    Refresh with ``--prod-stats <file.json>`` rather than editing, unless the numbers
    have moved enough to be worth committing. They drift slowly -- the counts moved
    under 1% against estimates taken from a dump ten days older.
    """

    works: int = 41_591_088
    editions: int = 56_728_501
    authors: int = 15_412_139
    deletes: int = 3_742_919
    redirects: int = 1_803_760
    lists: int = 264_531
    other: int = 95_045

    # Share of works with no `authors` at all; these cannot be author-anchored.
    authorless_work_rate: float = 0.0538
    # Share of editions with no `works` (the `sql/count-orphans.sql` population).
    # Refined from a full 1M-record sampling run; the prefix estimate said 3.84%.
    orphan_edition_rate: float = 0.0342
    # Author refs per work beyond the first, i.e. the co-authors an author-anchored
    # sample drags in on top of its anchors.
    coauthors_per_work: float = 0.1214
    # Share of the `other` dump that is `/type/subject`. The rest is schema types,
    # languages and series, which reference closure needs in full.
    subject_share_of_other: float = 0.9616

    @property
    def total(self) -> int:
        return self.works + self.editions + self.authors + self.deletes + self.redirects + self.lists + self.other


PROD_STATS = ProdStats()

# Field-level rates the sampler does not steer, but reports against so a regression in
# the scheme is visible. Measured independently of any sampling run, off a 262MB prefix
# of each dump, so the manifest's comparison stays a real check rather than the sample
# grading itself. A 1M-record sample reads ocaid at 11.3% against the 11.6% here; the
# two methods differ by about that much throughout.
PROD_OCAID_RATE = 0.1156
PROD_READING_LOG_RATE = 0.0800  # 3,314,590 works carry a reading-log row
PROD_RATINGS_RATE = 0.0169  # 701,043 works carry a rating

# `works` only appears as a top-level key on editions, so this is safe to run against
# the raw line and lets us skip parsing the ~96% of editions that have one.
RE_EDITION_WORK = re.compile(rb'"works":\s*\[\s*\{\s*"key":\s*"(/works/[^"]+)"')
RE_OL_KEY = re.compile(r"^/(works|books|authors)/OL(\d+)[WMA]$")
RE_OL_KEY_B = re.compile(rb"^/(works|books|authors)/OL(\d+)[WMA]$")
# The interned form has to keep the type: /works/OL18W, /books/OL18M and
# /authors/OL18A share a number but are three different documents.
KEY_TYPE_TAG = {"works": 0, "books": 1, "authors": 2}

# Distinct hash spaces, so that eg the author top-up does not land inside the set of
# authors already picked as anchors -- which would make it a no-op.
SALT_ANCHOR = "anchor"
SALT_AUTHOR_TOPUP = "author-topup"
SALT_ORPHAN = "orphan-edition"
SALT_SUBJECT = "subject"


def frac(key: str | bytes, salt: str) -> float:
    """
    Map a key to a stable fraction in [0, 1).

    blake2b rather than ``hash()`` (not stable across interpreters) or ``crc32``.
    crc32 is linear over GF(2), so keys that differ in only a couple of characters --
    exactly what a run of sequential OL ids looks like -- get correlated outputs: a
    block of 100 consecutive edition keys hashed to *zero* selections at a 24% rate.
    """
    if isinstance(key, str):
        key = key.encode()
    # Salt goes in the message, not blake2b's `salt=` -- that field truncates at 16
    # bytes, which would silently merge our longer salt names into one hash space.
    digest = hashlib.blake2b(salt.encode() + b":" + key, digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


class KeySet:
    """
    A set of Open Library keys, stored as ints where the key has the usual shape.

    Sampling keeps several million-key membership sets live at once; interning
    ``/works/OL123W`` as an int drops that from ~125 bytes per key to ~32. Accepts
    ``str`` or ``bytes`` so hot loops can stay on the undecoded line.
    """

    __slots__ = ("_ints", "_strs")

    def __init__(self) -> None:
        self._ints: set[int] = set()
        self._strs: set[str] = set()

    @staticmethod
    def _intern(key: str | bytes) -> int | None:
        """The ``(type, number)`` pair packed into one int, or None if unusual."""
        if isinstance(key, bytes):
            if m := RE_OL_KEY_B.match(key):
                return int(m[2]) * 4 + KEY_TYPE_TAG[m[1].decode()]
        elif m := RE_OL_KEY.match(key):
            return int(m[2]) * 4 + KEY_TYPE_TAG[m[1]]
        return None

    def add(self, key: str | bytes) -> None:
        if (packed := self._intern(key)) is not None:
            self._ints.add(packed)
        elif isinstance(key, bytes):
            self._strs.add(key.decode("utf-8", "replace"))
        else:
            self._strs.add(key)

    def update(self, keys: Iterable[str | bytes]) -> None:
        for key in keys:
            self.add(key)

    def __contains__(self, key: str | bytes) -> bool:
        if (packed := self._intern(key)) is not None:
            return packed in self._ints
        if isinstance(key, bytes):
            return key.decode("utf-8", "replace") in self._strs
        return key in self._strs

    def __len__(self) -> int:
        return len(self._ints) + len(self._strs)


# -----------------------------------------------------------------------------
# Reading dumps
# -----------------------------------------------------------------------------


def resolve_dump_item(source: str) -> tuple[str, str]:
    """
    Return ``(base, date)`` for a dump source.

    ``source`` is a local directory, an archive.org item URL, or "latest" to follow
    ``ol_dump_latest.txt.gz`` the way the Jenkinsfile does.
    """
    if source == "latest":
        url = subprocess.run(
            ["curl", "-sIL", LATEST_DUMP_URL, "-o", "/dev/null", "-w", "%{url_effective}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        filename = url.rsplit("/", 1)[-1]
        base = url.rsplit("/", 1)[0]
    elif source.startswith("http"):
        # eg .../download/ol_dump_2026-08-31 -> ol_dump_2026-08-31.txt.gz
        base = source.rstrip("/")
        filename = base.rsplit("/", 1)[-1] + ".txt.gz"
    else:
        base = source.rstrip("/")
        candidates = sorted(Path(base).glob("ol_dump_*.txt.gz"))
        if not candidates:
            raise ValueError(f"No ol_dump_*.txt.gz files in {base}")
        filename = candidates[0].name

    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if not m:
        raise ValueError(f"Could not work out the dump date from {filename!r}")
    return base, m[1]


def dump_location(base: str, date: str, name: str) -> str:
    """Location of one per-type dump. ``name`` of "" gives the combined dump."""
    stem = f"ol_dump_{name}_{date}" if name else f"ol_dump_{date}"
    return f"{base}/{stem}.txt.gz"


def http_size(url: str) -> int | None:
    """Content length of ``url``, or None if the server won't do byte ranges."""
    try:
        out = subprocess.run(
            ["curl", "-sIL", "--retry", "3", url],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        ).stdout
    except subprocess.SubprocessError, OSError:
        return None
    lengths = re.findall(r"(?im)^content-length:\s*(\d+)", out)
    if not lengths or not re.search(r"(?im)^accept-ranges:\s*bytes", out):
        return None
    return int(lengths[-1])


def fetch_ranges(url: str, size: int, connections: int, chunk: int) -> Iterator[bytes]:
    """
    Fetch ``url`` as ordered byte ranges over several connections at once.

    archive.org throttles hard per connection -- a single stream off the editions dump
    holds ~2.9MB/s, while eight across the item's two nodes reach ~11MB/s. Chunks come
    back in order so the result is still a plain byte stream that gzip can decompress
    on the fly, which is the whole point: nothing has to land on disk.

    (The item's torrent is not a shortcut here. Its webseeds are these same two hosts,
    a dump this niche has no peers to speak of, and a torrent client would have to
    write all 12.6GB to disk out of order before anything could be decompressed.)
    """
    hosts = re.match(r"https?://([^/]+)(/.*)$", url)
    urls = [url]
    if hosts and re.fullmatch(r"ia\d+\.us\.archive\.org", hosts[1]):
        # Every item is served from a pair of nodes; spreading across both roughly
        # doubles what a single node will give us.
        sibling = hosts[1].replace("ia8", "ia6", 1) if hosts[1].startswith("ia8") else hosts[1].replace("ia6", "ia8", 1)
        urls.append(f"http://{sibling}{hosts[2]}")

    n_chunks = (size + chunk - 1) // chunk

    def get(i: int) -> bytes:
        lo, hi = i * chunk, min(size, (i + 1) * chunk) - 1
        last = None
        for attempt in range(5):
            target = urls[(i + attempt) % len(urls)]
            try:
                out = subprocess.run(
                    ["curl", "-sfL", "--retry", "2", "-r", f"{lo}-{hi}", target],
                    capture_output=True,
                    timeout=600,
                    check=False,
                )
                if out.returncode == 0 and len(out.stdout) == hi - lo + 1:
                    return out.stdout
                last = OSError(f"range {lo}-{hi}: exit {out.returncode}, got {len(out.stdout)} bytes")
            except (subprocess.SubprocessError, OSError) as e:
                last = e
            time.sleep(2 * attempt)
        raise OSError(f"Could not fetch {url} range {lo}-{hi}") from last

    # Keep a bounded window in flight: enough to hide latency, not so much that the
    # buffered-but-not-yet-consumed chunks add up to real memory.
    window = connections + 4
    with ThreadPoolExecutor(max_workers=connections) as pool:
        pending = {i: pool.submit(get, i) for i in range(min(window, n_chunks))}
        try:
            for i in range(n_chunks):
                data = pending.pop(i).result()
                if (nxt := i + window) < n_chunks:
                    pending[nxt] = pool.submit(get, nxt)
                yield data
        finally:
            for future in pending.values():
                future.cancel()


def read_dump(location: str, max_lines: int = 0, connections: int = 1) -> Iterator[bytes]:
    """
    Stream a gzipped dump line by line, over HTTP or from disk.

    Shells out to gzip rather than using the ``gzip`` module: at 12.6GB for the
    editions dump alone, decompressing in-process roughly doubles the wall clock.

    Yields undecoded lines. At ~119M of them, decoding every line to pick 1% of them
    is real time; ``json.loads`` takes bytes and the lines we keep are copied through
    to the output byte for byte.
    """
    unzip = "pigz" if shutil.which("pigz") else "gzip"
    is_http = location.startswith("http")
    size = http_size(location) if is_http and connections > 1 else None

    if size:
        # Parallel ranges, piped into gzip through stdin.
        proc = subprocess.Popen(
            [unzip, "-dc"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1024 * 1024,
        )
        feeder_error: list[BaseException] = []

        def feed() -> None:
            assert proc.stdin is not None
            try:
                for block in fetch_ranges(location, size, connections, chunk=32 * 1024 * 1024):
                    proc.stdin.write(block)
            except BaseException as e:  # noqa: BLE001 - re-raised on the reading side
                feeder_error.append(e)
            finally:
                with contextlib.suppress(OSError):
                    proc.stdin.close()

        feeder = threading.Thread(target=feed, daemon=True)
        feeder.start()
    else:
        if is_http:
            cmd = f"curl -sfL --retry 5 --retry-delay 5 {location!r} | {unzip} -dc"
        else:
            cmd = f"{unzip} -dc {location!r}"
        proc = subprocess.Popen(
            f"set -o pipefail; {cmd}",
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            # A truncated tail is normal when we stop reading early; don't spam the log.
            stderr=subprocess.DEVNULL,
            bufsize=1024 * 1024,
        )
        feeder = feeder_error = None

    assert proc.stdout is not None
    stopped_early = False
    try:
        for i, raw in enumerate(proc.stdout):
            if max_lines and i >= max_lines:
                stopped_early = True
                break
            yield raw
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
        proc.wait()
        if feeder is not None:
            feeder.join(timeout=30)

    # A dropped connection partway through a 12.6GB stream would otherwise look
    # exactly like a shorter dump, and silently produce a sample full of dangling
    # references. pipefail makes curl's failure the pipeline's failure; the parallel
    # path surfaces it through the feeder thread instead.
    if stopped_early:
        return
    if feeder_error:
        raise OSError(f"Reading {location} failed") from feeder_error[0]
    if proc.returncode != 0:
        raise OSError(f"Reading {location} failed with exit code {proc.returncode}; the dump may be truncated")


def parse_row(line: bytes) -> tuple[bytes, bytes, dict] | None:
    """Split a dump line into ``(type, key, json)``, or None if it is malformed."""
    parts = line.split(b"\t", 4)
    if len(parts) < 5:
        return None
    try:
        return parts[0], parts[1], json.loads(parts[4])
    except ValueError:
        return None


class Progress:
    """Logs throughput every ``every`` lines so a multi-hour pass isn't silent."""

    def __init__(self, label: str, every: int = 2_000_000) -> None:
        self.label = label
        self.every = every
        self.seen = 0
        self.kept = 0
        self.start = time.time()

    def tick(self, kept: int = 0) -> None:
        self.seen += 1
        self.kept = kept
        if self.seen % self.every == 0:
            elapsed = time.time() - self.start
            logger.info(
                "%s: %s read, %s kept (%.0fk lines/s)",
                self.label,
                f"{self.seen:,}",
                f"{self.kept:,}",
                self.seen / elapsed / 1000,
            )

    def done(self) -> None:
        logger.info(
            "%s: done -- %s read, %s kept in %.1fs",
            self.label,
            f"{self.seen:,}",
            f"{self.kept:,}",
            time.time() - self.start,
        )


# -----------------------------------------------------------------------------
# Sampling
# -----------------------------------------------------------------------------


@dataclass
class Rates:
    """Per-type inclusion rates, chosen so the sample lands near ``target_records``."""

    work: float
    orphan_edition: float
    author_topup: float
    delete: float
    redirect: float
    list: float
    subject: float

    @staticmethod
    def calibrate(target: int, stats: ProdStats, anchor: str) -> Rates:
        """
        Solve for the base rate ``p`` that yields ``target`` records overall.

        Everything downstream of the anchor scales with ``p``, so the total is linear
        in it and we can predict each type's yield from the production ratios rather
        than iterating.
        """
        s = stats
        # Records the anchor pulls in, per unit of p.
        per_p = s.works + s.editions + s.deletes + s.redirects + s.lists + s.other
        if anchor == "author":
            # Anchors, plus co-authors dragged in by works, plus orphan editions'
            # authors -- all of which must be present for refs to resolve.
            per_p += s.authors + s.coauthors_per_work * s.works + s.orphan_edition_rate * s.editions
        else:
            # Every sampled work needs its own authors; sharing is mostly lost.
            per_p += (1 + s.coauthors_per_work) * s.works + s.orphan_edition_rate * s.editions

        p = min(1.0, target / per_p)
        # Authors arrive via reference closure; only top up if that undershoots the
        # production share, which it does not in practice at small p.
        return Rates(
            work=p,
            orphan_edition=p,
            author_topup=p,
            delete=p,
            redirect=p,
            list=p,
            subject=p,
        )


@dataclass
class Selection:
    """Everything the passes accumulate about what is in the sample."""

    works: KeySet = field(default_factory=KeySet)
    editions: KeySet = field(default_factory=KeySet)
    authors: KeySet = field(default_factory=KeySet)
    # Keys referenced by an included doc that must resolve to something.
    needed_authors: KeySet = field(default_factory=KeySet)
    needed_series: set[str] = field(default_factory=set)
    needed_languages: set[str] = field(default_factory=set)
    needed_covers: set[int] = field(default_factory=set)
    # Refs we could not satisfy from the authors/other dumps; redirects and deletes
    # get a chance to resolve them, exactly as they do in production.
    counts: dict[str, int] = field(default_factory=dict)

    def resolves(self, key: str) -> bool:
        return key in self.works or key in self.editions or key in self.authors


def iter_refs(type_: str | bytes, doc: dict) -> Iterator[tuple[str, str]]:
    """
    Yield ``(kind, key)`` for every reference the Solr indexer actually follows.

    Both the closure passes and ``verify`` go through here, so what the sampler
    promises to keep resolvable cannot drift from what gets checked.

    Type matters. ``update.py`` routes on ``type.key`` and only ever dereferences
    fields on works, editions and lists. Tombstones and the handful of mistyped
    records in the dump (``/type/doc`` and ``/type/macro`` rows carrying ``/books/``
    keys, authors still holding the legacy ``works`` field) keep stale fields that
    nothing reads -- treating those as references would demand documents production
    itself does not have.
    """
    if isinstance(type_, bytes):
        type_ = type_.decode("utf-8", "replace")

    if type_ == "/type/list":
        for seed in doc.get("seeds") or []:
            if isinstance(seed, dict):
                key = seed.get("key") or (seed.get("thing") or {}).get("key")
                if isinstance(key, str) and key.startswith("/"):
                    yield "seed", key
        return

    if type_ not in ("/type/work", "/type/edition"):
        return

    for author in doc.get("authors") or []:
        if not isinstance(author, dict):
            continue
        # Works nest as {"author": {"key": ...}}; editions are flat {"key": ...}.
        # `normalize_authors` accepts a bare string for the nested form too.
        ref = author.get("author", author)
        key = ref.get("key") if isinstance(ref, dict) else ref
        # Some records hold an author's *name* where a key belongs. That dangles in
        # production too, so reproduce it rather than trying to resolve it.
        if isinstance(key, str) and key.startswith("/authors/"):
            yield "author", key

    for excerpt in doc.get("excerpts") or []:
        if not isinstance(excerpt, dict) or not isinstance(excerpt.get("author"), dict):
            continue
        key = excerpt["author"].get("key")
        if isinstance(key, str) and key.startswith("/authors/"):
            yield "author", key

    for edge in doc.get("series") or []:
        if not isinstance(edge, dict) or not isinstance(edge.get("series"), dict):
            continue
        if isinstance(key := edge["series"].get("key"), str):
            yield "series", key

    for lang in (doc.get("languages") or []) + (doc.get("translated_from") or []):
        if isinstance(lang, dict) and isinstance(key := lang.get("key"), str):
            yield "language", key

    if type_ == "/type/edition":
        for work in doc.get("works") or []:
            if isinstance(work, dict) and isinstance(key := work.get("key"), str):
                yield "work", key


def collect_refs(type_: str | bytes, doc: dict, sel: Selection) -> None:
    """Record every key ``doc`` references that reference closure has to satisfy."""
    for kind, key in iter_refs(type_, doc):
        if kind == "author":
            sel.needed_authors.add(key)
        elif kind == "series":
            sel.needed_series.add(key)
        elif kind == "language":
            sel.needed_languages.add(key)

    # Not a reference in the breaking sense -- `get_cover_dimensions` returns None
    # for an unknown id -- but the covers dump is filtered to these.
    for cover in doc.get("covers") or []:
        if isinstance(cover, int) and cover > 0:
            sel.needed_covers.add(cover)


def first_author_key(doc: dict) -> str | None:
    for author in doc.get("authors") or []:
        if not isinstance(author, dict):
            continue
        ref = author.get("author", author)
        key = ref.get("key") if isinstance(ref, dict) else ref
        if isinstance(key, str) and key.startswith("/authors/"):
            return key
    return None


class Sampler:
    def __init__(
        self,
        base: str,
        date: str,
        out_dir: Path,
        rates: Rates,
        stats: ProdStats,
        anchor: str,
        salt: str,
        max_source_lines: int = 0,
        connections: int = 8,
    ) -> None:
        self.base = base
        self.date = date
        self.out_dir = out_dir
        self.rates = rates
        self.stats = stats
        self.anchor = anchor
        self.salt = salt
        self.max_source_lines = max_source_lines
        self.connections = connections
        self.sel = Selection()

    # -- plumbing ------------------------------------------------------------

    def source(self, name: str) -> str:
        return dump_location(self.base, self.date, name)

    def out_path(self, name: str) -> Path:
        stem = f"ol_dump_{name}_{self.date}" if name else f"ol_dump_{self.date}"
        return self.out_dir / f"{stem}.txt.gz"

    def _writer(self, name: str):
        return gzip.open(self.out_path(name), "wb", compresslevel=6)

    def _read(self, name: str) -> Iterator[bytes]:
        return read_dump(self.source(name), self.max_source_lines, self.connections)

    def _record(self, name: str, count: int) -> None:
        self.sel.counts[name] = count

    def frac(self, key: str | bytes, space: str) -> float:
        return frac(key, f"{self.salt}:{space}")

    # -- passes --------------------------------------------------------------

    def pass_works(self) -> None:
        """
        Select works and note what they reference.

        Author-anchored: a work is in iff its *first* author is. Works with no author
        fall back to a hash of their own key, at the same rate, so the authorless share
        is preserved.
        """
        prog = Progress("works")
        kept = 0
        with self._writer("works") as out:
            for line in self._read("works"):
                prog.tick(kept)
                row = parse_row(line)
                if row is None:
                    continue
                _type, key, doc = row

                if self.anchor == "author" and (author := first_author_key(doc)):
                    include = self.frac(author, SALT_ANCHOR) < self.rates.work
                else:
                    include = self.frac(key, SALT_ANCHOR) < self.rates.work
                if not include:
                    continue

                out.write(line)
                kept += 1
                self.sel.works.add(key)
                collect_refs(_type, doc, self.sel)
        prog.kept = kept
        prog.done()
        self._record("works", kept)

    def pass_editions(self) -> None:
        """
        Select every edition of a selected work, plus a hash-sampled share of orphans.

        Taking *all* of a selected work's editions is what keeps the editions-per-work
        distribution and the edition:work ratio intact; sampling editions
        independently would flatten both.
        """
        prog = Progress("editions", every=5_000_000)
        kept = 0
        orphans = 0
        ocaids = 0
        with self._writer("editions") as out:
            for line in self._read("editions"):
                prog.tick(kept)

                # Fast path: nearly all editions have a work, and finding its key in
                # the raw line avoids parsing the ~96% we will mostly reject.
                if m := RE_EDITION_WORK.search(line):
                    if m[1] not in self.sel.works:
                        continue
                    row = parse_row(line)
                    if row is None:
                        continue
                    _type, key, doc = row
                else:
                    row = parse_row(line)
                    if row is None:
                        continue
                    _type, key, doc = row
                    work_key = (doc.get("works") or [{}])[0].get("key")
                    if work_key:
                        # Regex missed it (unusual formatting); re-check properly.
                        if work_key not in self.sel.works:
                            continue
                    else:
                        if self.frac(key, SALT_ORPHAN) >= self.rates.orphan_edition:
                            continue
                        orphans += 1

                out.write(line)
                kept += 1
                ocaids += bool(doc.get("ocaid"))
                self.sel.editions.add(key)
                collect_refs(_type, doc, self.sel)
        prog.kept = kept
        prog.done()
        self._record("editions", kept)
        self._record("_orphan_editions", orphans)
        self._record("_ocaid_editions", ocaids)

    def pass_authors(self) -> None:
        """Emit every referenced author, plus a top-up to reach the production share."""
        target = int(self.rates.author_topup * self.stats.authors)
        shortfall = max(0, target - len(self.sel.needed_authors))
        topup_rate = shortfall / self.stats.authors
        logger.info(
            "authors: %s referenced, target %s -> topping up at %.5f%%",
            f"{len(self.sel.needed_authors):,}",
            f"{target:,}",
            100 * topup_rate,
        )

        prog = Progress("authors", every=5_000_000)
        kept = 0
        with self._writer("authors") as out:
            for line in self._read("authors"):
                prog.tick(kept)
                parts = line.split(b"\t", 3)
                if len(parts) < 3:
                    continue
                key = parts[1]
                if key not in self.sel.needed_authors and self.frac(key, SALT_AUTHOR_TOPUP) >= topup_rate:
                    continue
                out.write(line)
                kept += 1
                self.sel.authors.add(key)
        prog.kept = kept
        prog.done()
        self._record("authors", kept)

    def pass_other(self) -> None:
        """
        Emit the `other` dump.

        Everything that isn't a ``/type/subject`` is kept whole: it is only ~3.6k rows
        and it holds the schema types, languages and series that other records point
        at. Subjects are hash-sampled -- nothing references them (the pipeline derives
        its subject docs from Solr facets in ``index_subjects.py``).
        """
        prog = Progress("other", every=100_000)
        kept = 0
        subjects = 0
        with self._writer("other") as out:
            for line in self._read("other"):
                prog.tick(kept)
                parts = line.split(b"\t", 3)
                if len(parts) < 3:
                    continue
                type_, key = parts[0], parts[1]
                if type_ == b"/type/subject":
                    if self.frac(key, SALT_SUBJECT) >= self.rates.subject:
                        continue
                    subjects += 1
                out.write(line)
                kept += 1
        prog.kept = kept
        prog.done()
        self._record("other", kept)
        self._record("_other_subjects", subjects)

    def pass_redirects(self) -> None:
        """
        Emit every redirect whose target is in the sample.

        Deliberately no hash gate on top of the target check. A redirect points at one
        arbitrary document, so it lands in the sample with probability ~p already --
        keeping all of them yields the production share on its own. Gating on a hash
        as well makes it p squared, which under-sampled redirects roughly 40-fold.

        Also emits any redirect that satisfies an outstanding author ref: production
        has works pointing at author keys that turn out to be redirects, and
        ``WorkSolrUpdater`` copes with that (it filters on ``type.key``) but not with
        the key being absent entirely.
        """
        prog = Progress("redirects", every=1_000_000)
        target = max(1, int(self.rates.redirect * self.stats.redirects))
        mandatory: list[bytes] = []
        candidates: list[tuple[float, bytes]] = []

        for line in self._read("redirects"):
            prog.tick(len(mandatory) + len(candidates))
            row = parse_row(line)
            if row is None:
                continue
            _type, key, doc = row

            if key in self.sel.needed_authors and key not in self.sel.authors:
                mandatory.append(line)
            elif isinstance(location := doc.get("location"), str) and self.sel.resolves(location):
                candidates.append((self.frac(key, SALT_ANCHOR), line))
        prog.done()

        # Redirects pile up on documents that absorbed merges, and those are exactly
        # the well-connected authors and works a reference-closed sample is most
        # likely to contain -- so "every redirect whose target is present" overshoots
        # the production share several-fold. Trim deterministically to the target.
        candidates.sort()
        lines = mandatory + [line for _, line in candidates[: max(0, target - len(mandatory))]]
        with self._writer("redirects") as out:
            for line in lines:
                out.write(line)
                key = line.split(b"\t", 3)[1]
                if key.startswith(b"/authors/"):
                    self.sel.authors.add(key)
        logger.info("redirects: kept %s of %s resolvable", f"{len(lines):,}", f"{len(candidates) + len(mandatory):,}")
        self._record("redirects", len(lines))

    def pass_deletes(self) -> None:
        """Emit hash-sampled tombstones, plus any that satisfy an outstanding ref."""
        prog = Progress("deletes", every=1_000_000)
        kept = 0
        with self._writer("deletes") as out:
            for line in self._read("deletes"):
                prog.tick(kept)
                parts = line.split(b"\t", 3)
                if len(parts) < 3:
                    continue
                key = parts[1]
                resolves_a_ref = key in self.sel.needed_authors and key not in self.sel.authors
                if not (resolves_a_ref or self.frac(key, SALT_ANCHOR) < self.rates.delete):
                    continue
                out.write(line)
                kept += 1
                if key.startswith(b"/authors/"):
                    self.sel.authors.add(key)
        prog.kept = kept
        prog.done()
        self._record("deletes", kept)

    def pass_lists(self) -> None:
        """
        Emit lists, pruned to the seeds that made it into the sample.

        A list's seeds are spread across the whole corpus, so at a sample rate of well
        under 1% a list almost never survives intact. Rather than drag every seed in
        (which would blow the record budget -- the largest list has 15k of them),
        seeds are pruned to what is present and lists left with none are dropped.
        Enough lists retain a seed that the *count* still tracks production; what is
        lost is the seeds-per-list distribution, which skews short.
        """
        target = max(1, int(self.rates.list * self.stats.lists))
        candidates: list[tuple[float, bytes]] = []
        prog = Progress("lists", every=100_000)

        for line in self._read("lists"):
            prog.tick(len(candidates))
            row = parse_row(line)
            if row is None:
                continue
            _type, key, doc = row

            kept_seeds = []
            for seed in doc.get("seeds") or []:
                if isinstance(seed, str):
                    # A bare string seed is a subject, not a key; always safe to keep.
                    kept_seeds.append(seed)
                elif isinstance(seed, dict):
                    ref = seed.get("key") or (seed.get("thing") or {}).get("key")
                    if isinstance(ref, str) and self.sel.resolves(ref):
                        kept_seeds.append(seed)
            if not kept_seeds:
                continue

            doc["seeds"] = kept_seeds
            parts = line.rstrip(b"\n").split(b"\t", 4)
            parts[4] = json.dumps(doc).encode()
            candidates.append((self.frac(key, SALT_ANCHOR), b"\t".join(parts) + b"\n"))

        # Deterministically take the best-hashing `target` survivors, so the list count
        # tracks production instead of being whatever the prune happened to leave.
        candidates.sort()
        with self._writer("lists") as out:
            for _, line in candidates[:target]:
                out.write(line)
        prog.kept = min(target, len(candidates))
        prog.done()
        self._record("lists", min(target, len(candidates)))

    # -- aux dumps -----------------------------------------------------------

    def pass_work_keyed_aux(self, name: str) -> None:
        """
        Filter ratings / reading-log to works in the sample.

        Rows are kept purely on their work key, which is independent of whether the
        work was sampled -- so the share of works carrying ratings or a reading log
        comes out at the production rate on its own. The edition column is only
        informational (nothing joins on it), so a reference to an edition outside the
        sample is nulled rather than dropping the row and skewing that share.
        """
        prog = Progress(name, every=5_000_000)
        kept = 0
        seen_works: set[bytes] = set()
        with self._writer(name) as out:
            for line in self._read(name):
                prog.tick(kept)
                parts = line.rstrip(b"\n").split(b"\t")
                if len(parts) < 2 or parts[0] not in self.sel.works:
                    continue
                if parts[1] != rb"\N" and parts[1] not in self.sel.editions:
                    parts[1] = rb"\N"
                out.write(b"\t".join(parts) + b"\n")
                kept += 1
                seen_works.add(parts[0])
        prog.kept = kept
        prog.done()
        self._record(name, kept)
        self._record(f"_{name}_works", len(seen_works))

    def pass_covers(self) -> None:
        """Filter cover metadata to the cover ids the sampled records point at."""
        prog = Progress("covers_metadata", every=5_000_000)
        kept = 0
        with self._writer("covers_metadata") as out:
            for line in self._read("covers_metadata"):
                prog.tick(kept)
                cover_id, _, rest = line.partition(b"\t")
                if not rest:
                    continue
                try:
                    if int(cover_id) not in self.sel.needed_covers:
                        continue
                except ValueError:
                    continue
                out.write(line)
                kept += 1
        prog.kept = kept
        prog.done()
        self._record("covers_metadata", kept)

    def write_combined(self) -> None:
        """
        Concatenate the per-type dumps into the combined dump the Jenkinsfile imports.

        Concatenated gzip members are a valid gzip stream, so this is a byte copy --
        and it mirrors the real combined dump, which is itself a concatenation of
        independently sorted chunks rather than one globally sorted file.
        """
        combined = self.out_path("")
        with open(combined, "wb") as out:
            for name in DUMP_TYPES:
                path = self.out_path(name)
                if path.exists():
                    with open(path, "rb") as f:
                        shutil.copyfileobj(f, out, 4 * 1024 * 1024)
        logger.info("combined dump: %s (%.1f MB)", combined, combined.stat().st_size / 1e6)

    def verify(self) -> int:
        """
        Re-read the emitted combined dump and check every reference resolves.

        The passes are meant to guarantee this by construction, but the guarantee is
        only as good as the ref extraction in ``collect_refs``, and a silently dropped
        work is exactly the failure this fixture exists to avoid. The sample is small,
        so checking is cheap -- do it rather than trust the construction.
        """
        prog = Progress("verify", every=1_000_000)
        present: set[str] = set()
        refs: list[tuple[str, str]] = []

        for line in read_dump(str(self.out_path(""))):
            prog.tick(len(present))
            row = parse_row(line)
            if row is None:
                continue
            _type, key_bytes, doc = row
            key = key_bytes.decode("utf-8", "replace")
            present.add(key)
            refs.extend((key, ref) for _kind, ref in iter_refs(_type, doc))
        prog.done()

        dangling = [(src, dst) for src, dst in refs if dst not in present]
        self._record("_dangling_refs", len(dangling))
        if dangling:
            logger.error("%s dangling references, eg:", f"{len(dangling):,}")
            for src, dst in dangling[:10]:
                logger.error("  %s -> %s (missing)", src, dst)
        else:
            logger.info("verify: all %s references resolve", f"{len(refs):,}")
        return len(dangling)

    def subset_osp(self, osp_dump: Path) -> None:
        """Copy the Open Syllabus totals for sampled works into a small sqlite db."""
        dest = self.out_dir / "osp_totals.db"
        dest.unlink(missing_ok=True)
        with sqlite3.connect(osp_dump) as src, sqlite3.connect(dest) as dst:
            dst.execute("CREATE TABLE data (olid INTEGER PRIMARY KEY, total INTEGER)")
            rows = [(olid, total) for olid, total in src.execute("SELECT olid, total FROM data") if f"/works/OL{olid}W" in self.sel.works]
            dst.executemany("INSERT INTO data VALUES (?, ?)", rows)
        self._record("osp_totals", len(rows))
        logger.info("osp_totals.db: %s rows", f"{len(rows):,}")


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------


def build_manifest(sampler: Sampler, target: int, elapsed: float) -> dict[str, Any]:
    stats = sampler.stats
    counts = sampler.sel.counts
    record_types = list(DUMP_TYPES)
    total = sum(counts.get(t, 0) for t in record_types)

    types = {}
    for name in record_types:
        got = counts.get(name, 0)
        types[name] = {
            "count": got,
            "share": round(got / total, 6) if total else 0,
            "prod_share": round(getattr(stats, name) / stats.total, 6),
        }

    editions = counts.get("editions", 0) or 1
    works = counts.get("works", 0) or 1

    def ratio(got: int, denom: int, prod: float) -> dict[str, float]:
        return {"sample": round(got / denom, 5), "prod": prod}

    return {
        "generated_from": {"base": sampler.base, "date": sampler.date},
        "target_records": target,
        "total_records": total,
        "anchor": sampler.anchor,
        "salt": sampler.salt,
        "elapsed_seconds": round(elapsed, 1),
        "types": types,
        "ratios": {
            "orphan_editions": ratio(counts.get("_orphan_editions", 0), editions, stats.orphan_edition_rate),
            "editions_with_ocaid": ratio(counts.get("_ocaid_editions", 0), editions, PROD_OCAID_RATE),
            "works_with_reading_log": ratio(counts.get("_reading-log_works", 0), works, PROD_READING_LOG_RATE),
            "works_with_ratings": ratio(counts.get("_ratings_works", 0), works, PROD_RATINGS_RATE),
        },
        "dangling_refs": counts.get("_dangling_refs"),
        "aux_rows": {name: counts.get(name, 0) for name in AUX_DUMPS},
        "osp_totals": counts.get("osp_totals"),
    }


def print_summary(manifest: dict[str, Any]) -> None:
    print()
    print(f"Sampled {manifest['total_records']:,} records (target {manifest['target_records']:,})")
    print()
    print(f"  {'type':<12} {'count':>12} {'share':>8} {'prod':>8}")
    for name, info in manifest["types"].items():
        print(f"  {name:<12} {info['count']:>12,} {100 * info['share']:>7.2f}% {100 * info['prod_share']:>7.2f}%")
    print()
    for name, info in manifest["ratios"].items():
        if "prod" in info:
            print(f"  {name:<24} {100 * info['sample']:>7.2f}%  (prod {100 * info['prod']:.2f}%)")
    print()
    for name, rows in manifest["aux_rows"].items():
        print(f"  {name:<24} {rows:>12,} rows")


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def main(
    target_records: int,
    out_dir: Path = Path("sample_dumps"),
    source: str = "latest",
    anchor: Literal["author", "work"] = "author",
    salt: str = "openlibrary",
    osp_dump: Path | None = None,
    prod_stats: Path | None = None,
    max_source_lines: int = 0,
    connections: int = 8,
    verify: bool = True,
    log_level: str = "INFO",
) -> None:
    """
    Build a scaled-down, reference-complete copy of the Open Library dumps.

    :param target_records: Approximate number of records to emit (eg 1000000).
    :param out_dir: Directory to write the sampled dump set into.
    :param source: "latest" to follow openlibrary.org's latest dump, or a local
        directory / archive.org item URL holding the per-type dumps.
    :param anchor: What to sample on. "author" keeps whole bibliographies together and
        tracks the production author:work ratio far better; "work" samples works
        independently.
    :param salt: Changes which records are picked, without changing the ratios.
    :param osp_dump: Optional Open Syllabus osp_totals.db to subset alongside.
    :param prod_stats: Optional JSON overriding the measured production figures.
    :param max_source_lines: Read at most this many lines per source dump. For
        smoke-testing the script; produces a badly skewed sample.
    :param connections: Parallel HTTP range requests per dump. archive.org throttles
        per connection, so 8 is roughly 4x a single stream; 1 disables it. Ignored for
        local sources. Buffers up to (connections + 4) * 32MB.
    :param verify: Re-read the output and fail if any reference dangles.
    :param log_level: Python logging level.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stderr,
    )

    stats = PROD_STATS
    if prod_stats:
        stats = ProdStats(**json.loads(prod_stats.read_text()))

    base, date = resolve_dump_item(source)
    rates = Rates.calibrate(target_records, stats, anchor)
    logger.info("source %s (dump %s), base rate %.6f%%", base, date, 100 * rates.work)

    out_dir.mkdir(parents=True, exist_ok=True)
    sampler = Sampler(base, date, out_dir, rates, stats, anchor, salt, max_source_lines, connections)

    start = time.time()
    sampler.pass_works()
    sampler.pass_editions()
    sampler.pass_authors()
    sampler.pass_other()
    sampler.pass_redirects()
    sampler.pass_deletes()
    sampler.pass_lists()
    sampler.pass_work_keyed_aux("ratings")
    sampler.pass_work_keyed_aux("reading-log")
    sampler.pass_covers()
    sampler.write_combined()
    if osp_dump:
        sampler.subset_osp(osp_dump)
    dangling = sampler.verify() if verify else 0

    manifest = build_manifest(sampler, target_records, time.time() - start)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print_summary(manifest)
    print(f"\nWrote {out_dir}/ -- point the pipeline's DUMP_DIR at it.")
    if dangling:
        sys.exit(f"FAILED: {dangling:,} dangling references in the sample")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.solr_builder.solr_builder.fn_to_cli import FnToCLI

    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    FnToCLI(main).run()
