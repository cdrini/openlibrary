import gzip
import json
from pathlib import Path

import pytest

from scripts.solr_builder.make_sample_dumps import (
    SALT_ANCHOR,
    SALT_AUTHOR_TOPUP,
    KeySet,
    ProdStats,
    Rates,
    Selection,
    collect_refs,
    first_author_key,
    frac,
    main,
    parse_row,
    resolve_dump_item,
)

DATE = "2020-01-01"


def dump_line(type_: str, key: str, doc: dict) -> str:
    return "\t".join([type_, key, "1", f"{DATE}T00:00:00.000000", json.dumps(doc)]) + "\n"


def write_dump(dir: Path, name: str, lines: list[str]) -> None:
    stem = f"ol_dump_{name}_{DATE}" if name else f"ol_dump_{DATE}"
    with gzip.open(dir / f"{stem}.txt.gz", "wt") as f:
        f.writelines(lines)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """
    A miniature corpus with the shapes the sampler has to get right.

    500 authors, each with 2 works; every third work has a co-author, so reference
    closure has to reach past the anchor. Works have 2 editions each, plus a
    population of orphans, and every 5th work carries ratings/reading-log rows.
    """
    src = tmp_path / "src"
    src.mkdir()

    works, editions, authors, ratings, reading_log, covers = [], [], [], [], [], []

    for a in range(500):
        authors.append(dump_line("/type/author", f"/authors/OL{a}A", {"type": {"key": "/type/author"}, "name": f"Author {a}"}))
    # Co-authors live outside the anchor range, so they can only arrive via closure.
    for a in range(1000, 1200):
        authors.append(dump_line("/type/author", f"/authors/OL{a}A", {"type": {"key": "/type/author"}, "name": f"Coauthor {a}"}))

    for w in range(1000):
        author_refs = [{"type": {"key": "/type/author_role"}, "author": {"key": f"/authors/OL{w % 500}A"}}]
        if w % 3 == 0:
            author_refs.append({"type": {"key": "/type/author_role"}, "author": {"key": f"/authors/OL{1000 + w % 200}A"}})
        works.append(
            dump_line(
                "/type/work",
                f"/works/OL{w}W",
                {"type": {"key": "/type/work"}, "title": f"Work {w}", "authors": author_refs, "covers": [w]},
            )
        )
        for e in range(2):
            doc = {
                "type": {"key": "/type/edition"},
                "title": f"Edition {w}-{e}",
                "works": [{"key": f"/works/OL{w}W"}],
                "authors": [{"key": f"/authors/OL{w % 500}A"}],
                "languages": [{"key": "/languages/eng"}],
            }
            if e == 0:
                doc["ocaid"] = f"work{w}edition{e}"
            editions.append(dump_line("/type/edition", f"/books/OL{w * 10 + e}M", doc))
        if w % 5 == 0:
            ratings.append(f"/works/OL{w}W\t/books/OL{w * 10}M\t4\t{DATE}\n")
            reading_log.append(f"/works/OL{w}W\t\\N\tWant to Read\t{DATE}\n")
        covers.append(f"{w}\t100\t200\t{DATE}\n")

    # Orphaned editions: no `works`, so they can only be picked by their own key.
    for o in range(100):
        editions.append(
            dump_line(
                "/type/edition",
                f"/books/OL{900000 + o}M",
                {"type": {"key": "/type/edition"}, "title": f"Orphan {o}", "authors": [{"key": f"/authors/OL{o % 500}A"}]},
            )
        )

    write_dump(src, "works", works)
    write_dump(src, "editions", editions)
    write_dump(src, "authors", authors)
    write_dump(src, "other", [dump_line("/type/language", "/languages/eng", {"type": {"key": "/type/language"}, "name": "English"})])
    write_dump(src, "redirects", [dump_line("/type/redirect", "/works/OL5000W", {"type": {"key": "/type/redirect"}, "location": "/works/OL1W"})])
    write_dump(src, "deletes", [dump_line("/type/delete", f"/works/OL{6000 + i}W", {"type": {"key": "/type/delete"}}) for i in range(50)])
    write_dump(
        src,
        "lists",
        [
            dump_line(
                "/type/list",
                f"/people/someone/lists/OL{i}L",
                {"type": {"key": "/type/list"}, "name": f"List {i}", "seeds": [{"key": f"/works/OL{j}W"} for j in range(i, i + 40)]},
            )
            for i in range(60)
        ],
    )
    with gzip.open(src / f"ol_dump_ratings_{DATE}.txt.gz", "wt") as f:
        f.writelines(ratings)
    with gzip.open(src / f"ol_dump_reading-log_{DATE}.txt.gz", "wt") as f:
        f.writelines(reading_log)
    with gzip.open(src / f"ol_dump_covers_metadata_{DATE}.txt.gz", "wt") as f:
        f.writelines(covers)
    write_dump(src, "", [])  # combined dump, so the date can be discovered
    return src


def read_out(out_dir: Path, name: str) -> list[tuple[str, str, dict]]:
    stem = f"ol_dump_{name}_{DATE}" if name else f"ol_dump_{DATE}"
    with gzip.open(out_dir / f"{stem}.txt.gz", "rb") as f:
        rows = [parse_row(line) for line in f]
    return [(t.decode(), k.decode(), d) for t, k, d in rows if rows]


class TestHelpers:
    def test_frac_is_stable_and_bounded(self):
        assert frac("/works/OL1W", "a") == frac(b"/works/OL1W", "a")
        assert 0 <= frac("/works/OL1W", "a") < 1

    def test_frac_salts_are_independent(self):
        # The whole point of the salts: the top-up must not land inside the anchors.
        keys = [f"/authors/OL{i}A" for i in range(2000)]
        anchors = {k for k in keys if frac(k, SALT_ANCHOR) < 0.1}
        topups = {k for k in keys if frac(k, SALT_AUTHOR_TOPUP) < 0.1}
        assert anchors
        assert topups
        assert len(anchors & topups) < 0.5 * len(anchors)

    def test_key_set_accepts_str_and_bytes(self):
        ks = KeySet()
        ks.add("/works/OL1W")
        ks.add(b"/authors/OL2A")
        ks.add("/people/someone/lists/OL3L")
        assert "/works/OL1W" in ks
        assert b"/works/OL1W" in ks
        assert "/authors/OL2A" in ks
        assert "/people/someone/lists/OL3L" in ks
        assert "/works/OL9W" not in ks
        assert len(ks) == 3

    def test_first_author_key_handles_both_shapes(self):
        work = {"authors": [{"type": {"key": "/type/author_role"}, "author": {"key": "/authors/OL1A"}}]}
        edition = {"authors": [{"key": "/authors/OL2A"}]}
        legacy = {"authors": [{"author": "/authors/OL3A"}]}
        assert first_author_key(work) == "/authors/OL1A"
        assert first_author_key(edition) == "/authors/OL2A"
        assert first_author_key(legacy) == "/authors/OL3A"
        assert first_author_key({"authors": []}) is None

    def test_collect_refs_picks_up_every_kind(self):
        sel = Selection()
        collect_refs(
            {
                "authors": [{"author": {"key": "/authors/OL1A"}}],
                "excerpts": [{"author": {"key": "/authors/OL2A"}}],
                "series": [{"series": {"key": "/series/OL1S"}}],
                "languages": [{"key": "/languages/eng"}],
                "translated_from": [{"key": "/languages/fre"}],
                "covers": [7, -1],
            },
            sel,
        )
        assert "/authors/OL1A" in sel.needed_authors
        assert "/authors/OL2A" in sel.needed_authors
        assert sel.needed_series == {"/series/OL1S"}
        assert sel.needed_languages == {"/languages/eng", "/languages/fre"}
        assert sel.needed_covers == {7}

    def test_resolve_dump_item_reads_the_date_off_the_dump(self, source: Path):
        base, date = resolve_dump_item(str(source))
        assert date == DATE
        assert base == str(source)

    def test_calibrate_scales_with_the_target(self):
        stats = ProdStats()
        small = Rates.calibrate(1_000_000, stats, "author")
        big = Rates.calibrate(10_000_000, stats, "author")
        assert big.work == pytest.approx(10 * small.work, rel=1e-6)
        # Anchoring on works needs its own authors per work, so it samples less deeply
        # for the same budget.
        assert Rates.calibrate(1_000_000, stats, "work").work < small.work


class TestSampling:
    @pytest.fixture
    def out_dir(self, source: Path, tmp_path: Path) -> Path:
        out = tmp_path / "out"
        stats = tmp_path / "stats.json"
        stats.write_text(json.dumps({"works": 1000, "editions": 2100, "authors": 700, "deletes": 50, "redirects": 1, "lists": 60, "other": 1}))
        main(1000, out_dir=out, source=str(source), prod_stats=stats, log_level="WARNING")
        return out

    def test_no_dangling_references(self, out_dir: Path):
        manifest = json.loads((out_dir / "manifest.json").read_text())
        assert manifest["dangling_refs"] == 0

    def test_every_edition_of_a_sampled_work_comes_along(self, out_dir: Path):
        work_keys = {key for _, key, _ in read_out(out_dir, "works")}
        editions = read_out(out_dir, "editions")
        for key in work_keys:
            w = key.removeprefix("/works/OL").removesuffix("W")
            got = {k for _, k, _ in editions if k in (f"/books/OL{int(w) * 10}M", f"/books/OL{int(w) * 10 + 1}M")}
            assert len(got) == 2, f"{key} lost an edition"

    def test_orphan_editions_are_sampled_independently(self, out_dir: Path):
        editions = read_out(out_dir, "editions")
        orphans = [k for _, k, doc in editions if not doc.get("works")]
        # 100 orphans in a 1000-record universe sampled whole; all should survive.
        assert orphans
        assert all(int(k.removeprefix("/books/OL").removesuffix("M")) >= 900000 for k in orphans)

    def test_co_authors_are_pulled_in_by_closure(self, out_dir: Path):
        author_keys = {key for _, key, _ in read_out(out_dir, "authors")}
        works = read_out(out_dir, "works")
        referenced = {a["author"]["key"] for _, _, doc in works for a in doc.get("authors", [])}
        assert referenced - author_keys == set()
        # And the corpus does exercise the co-author path.
        assert any(int(k.removeprefix("/authors/OL").removesuffix("A")) >= 1000 for k in author_keys)

    def test_aux_dumps_are_filtered_to_sampled_works(self, out_dir: Path):
        work_keys = {key for _, key, _ in read_out(out_dir, "works")}
        for name in ("ratings", "reading-log"):
            with gzip.open(out_dir / f"ol_dump_{name}_{DATE}.txt.gz", "rt") as f:
                rows = [line.split("\t") for line in f]
            assert rows
            assert all(row[0] in work_keys for row in rows)

    def test_edition_column_is_nulled_when_the_edition_is_absent(self, out_dir: Path):
        edition_keys = {key for _, key, _ in read_out(out_dir, "editions")}
        with gzip.open(out_dir / f"ol_dump_ratings_{DATE}.txt.gz", "rt") as f:
            for line in f:
                edition = line.split("\t")[1]
                assert edition == "\\N" or edition in edition_keys

    def test_covers_are_filtered_to_referenced_ids(self, out_dir: Path):
        referenced = {c for _, _, doc in read_out(out_dir, "works") for c in doc.get("covers", [])}
        with gzip.open(out_dir / f"ol_dump_covers_metadata_{DATE}.txt.gz", "rt") as f:
            ids = {int(line.split("\t")[0]) for line in f}
        assert ids == referenced

    def test_list_seeds_are_pruned_to_what_is_present(self, out_dir: Path):
        present = {key for _, key, _ in read_out(out_dir, "works")} | {key for _, key, _ in read_out(out_dir, "editions")}
        lists = read_out(out_dir, "lists")
        assert lists
        for _, _, doc in lists:
            seeds = doc["seeds"]
            assert seeds, "a list with no surviving seeds should have been dropped"
            assert all(seed["key"] in present for seed in seeds)

    def test_combined_dump_holds_every_type(self, out_dir: Path):
        types = {t for t, _, _ in read_out(out_dir, "")}
        assert {"/type/work", "/type/edition", "/type/author", "/type/list", "/type/delete"} <= types

    def test_manifest_reports_ratios_against_production(self, out_dir: Path):
        manifest = json.loads((out_dir / "manifest.json").read_text())
        assert manifest["total_records"] > 0
        assert set(manifest["types"]) == {"works", "editions", "authors", "other", "redirects", "deletes", "lists"}
        for ratio in manifest["ratios"].values():
            assert 0 <= ratio["sample"] <= 1
