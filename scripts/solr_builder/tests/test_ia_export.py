import gzip
import json

import httpx
import pytest

from openlibrary.solr.updater.work import full_ia_metadata_to_lite_metadata
from scripts.solr_builder.solr_builder import ia_export
from scripts.solr_builder.solr_builder.solr_builder import LocalPostgresDataProvider

ITEM_A = {
    "identifier": "aaa00",
    "collection": ["inlibrary", "printdisabled"],
    "boxid": "IA1",
    "access-restricted-item": "true",
}
ITEM_B = {"identifier": "bbb00", "collection": ["americana"], "boxid": ["IA2", "IA3"]}
ITEM_C = {"identifier": "ccc00", "collection": ["opensource"]}


def page(items, total=3, cursor=None):
    p = {"items": items, "count": len(items), "total": total}
    if cursor:
        p["cursor"] = cursor
    return p


class FakeIA:
    """Serves scrape pages by cursor; `failures` responses are served first."""

    def __init__(self, pages: dict, failures: list | None = None):
        self.pages = pages
        self.failures = list(failures or [])
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures:
            return self.failures.pop(0)
        return httpx.Response(200, json=self.pages[request.url.params.get("cursor")])


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(ia_export.time, "sleep", lambda s: None)


@pytest.fixture
def mock_client(monkeypatch):
    def install(handler):
        real_client = httpx.Client
        monkeypatch.setattr(
            ia_export.httpx,
            "Client",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )

    return install


def read_output(path):
    with gzip.open(path, "rt") as f:
        return [line.rstrip("\n").split("\t", 1) for line in f]


class TestValidatePage:
    def test_ok(self):
        assert ia_export.validate_page(page([ITEM_A]), 10000) == [ITEM_A]

    @pytest.mark.parametrize(
        "bad",
        [
            {"count": 0, "total": 1},
            {"items": [ITEM_A], "count": 2, "total": 1},
            {"items": [{"collection": ["x"]}], "count": 1, "total": 1},
            {"items": [{"identifier": ""}], "count": 1, "total": 1},
            {"items": [{"identifier": "a\tb"}], "count": 1, "total": 1},
        ],
    )
    def test_bad(self, bad):
        with pytest.raises(ia_export.ScrapePageError):
            ia_export.validate_page(bad, 10000)

    def test_more_than_requested(self):
        with pytest.raises(ia_export.ScrapePageError):
            ia_export.validate_page(page([ITEM_A, ITEM_B]), 1)


class TestMain:
    def test_follows_cursors(self, tmp_path, no_sleep, mock_client):
        fake = FakeIA({None: page([ITEM_A, ITEM_B], cursor="c1"), "c1": page([ITEM_C])})
        mock_client(fake)
        out = tmp_path / "ia.tsv.gz"

        ia_export.main(out)

        rows = read_output(out)
        assert [r[0] for r in rows] == ["aaa00", "bbb00", "ccc00"]
        assert [json.loads(r[1]) for r in rows] == [ITEM_A, ITEM_B, ITEM_C]
        assert not (tmp_path / "ia.tsv.gz.partial").exists()

        first, second = fake.requests
        assert first.url.params["q"] == "openlibrary_edition:*"
        assert first.url.params["count"] == "10000"
        assert first.url.params["fields"] == "identifier,boxid,collection,access-restricted-item"
        assert "cursor" not in first.url.params
        assert second.url.params["cursor"] == "c1"
        assert first.headers["x-application-id"] == "ol-solr"

    def test_retries_bad_responses(self, tmp_path, no_sleep, mock_client):
        fake = FakeIA(
            {None: page([ITEM_A, ITEM_B, ITEM_C])},
            failures=[
                httpx.Response(503),
                httpx.Response(200, text="not json"),
                # The API's response when it ignores the query
                httpx.Response(200, json={"items": [{"identifier": "x"}], "count": 2}),
            ],
        )
        mock_client(fake)
        out = tmp_path / "ia.tsv.gz"

        ia_export.main(out, retries=3)

        assert len(read_output(out)) == 3
        assert len(fake.requests) == 4

    def test_gives_up_without_publishing(self, tmp_path, no_sleep, mock_client):
        mock_client(FakeIA({}, failures=[httpx.Response(500)] * 3))
        out = tmp_path / "ia.tsv.gz"

        with pytest.raises(httpx.HTTPStatusError):
            ia_export.main(out, retries=2)
        assert not out.exists()

    def test_short_export_exits_nonzero(self, tmp_path, no_sleep, mock_client):
        mock_client(FakeIA({None: page([ITEM_A], total=1000)}))
        out = tmp_path / "ia.tsv.gz"

        with pytest.raises(SystemExit):
            ia_export.main(out)
        # Still usable; the builder falls back to live lookups for what's missing
        assert len(read_output(out)) == 1

    def test_max_pages(self, tmp_path, no_sleep, mock_client):
        fake = FakeIA({None: page([ITEM_A], total=1000, cursor="c1")})
        mock_client(fake)
        out = tmp_path / "ia.tsv.gz"

        ia_export.main(out, max_pages=1)

        assert len(fake.requests) == 1
        assert len(read_output(out)) == 1


class FakeCursor:
    def __init__(self, table_rows):
        self.table_rows = table_rows
        self.queries = []
        self.result = []

    def execute(self, q, params=None):
        self.queries.append(q)
        if "to_regclass" in q:
            self.result = [("ia_metadata" if self.table_rows is not None else None,)]
        else:
            self.result = [(row["identifier"], row) for row in self.table_rows or [] if row["identifier"] in params[0]]

    def fetchone(self):
        return self.result[0]

    def fetchall(self):
        return self.result

    def close(self):
        pass


@pytest.fixture
def provider(tmp_path):
    conf = tmp_path / "db.ini"
    conf.write_text("[postgres]\nhost=localhost\n")
    return LocalPostgresDataProvider(str(conf))


def setup_provider(provider, monkeypatch, table_rows):
    cursor = FakeCursor(table_rows)
    provider._conn = type("FakeConn", (), {"cursor": lambda self: cursor})()
    fetched = []

    async def fake_preload(ocaids):
        fetched.append(sorted(ocaids))
        for ocaid in ocaids:
            provider.ia_cache[ocaid] = {"identifier": ocaid, "collection": "fetched"}

    monkeypatch.setattr(provider, "preload_metadata", fake_preload)
    provider.cache.update(
        {
            "/books/OL1M": {"key": "/books/OL1M", "ocaid": "aaa00"},
            "/books/OL2M": {"key": "/books/OL2M", "ocaid": "zzz00"},
            "/books/OL3M": {"key": "/books/OL3M"},
        }
    )
    return cursor, fetched


class TestExportLookup:
    @pytest.mark.asyncio
    async def test_only_fetches_missing(self, provider, monkeypatch):
        _, fetched = setup_provider(provider, monkeypatch, [ITEM_A, ITEM_B])

        await provider.cache_cached_editions_ia_metadata()

        assert fetched == [["zzz00"]]
        assert provider.ia_cache["aaa00"] == ITEM_A
        assert provider.ia_cache["zzz00"]["collection"] == "fetched"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("table_rows", [None, []], ids=["no table", "empty table"])
    async def test_without_export_fetches_all(self, provider, monkeypatch, table_rows):
        cursor, fetched = setup_provider(provider, monkeypatch, table_rows)

        await provider.cache_cached_editions_ia_metadata()

        assert fetched == [["aaa00", "zzz00"]]
        if table_rows is None:
            assert len(cursor.queries) == 1


def test_lite_metadata_parity():
    # Shapes as returned by the scrape API, which match advancedsearch's docs
    assert full_ia_metadata_to_lite_metadata(ITEM_A) == {
        "boxid": {"IA1"},
        "collection": {"inlibrary", "printdisabled"},
        "access_restricted_item": "true",
    }
    assert full_ia_metadata_to_lite_metadata(ITEM_B) == {
        "boxid": {"IA2", "IA3"},
        "collection": {"americana"},
        "access_restricted_item": None,
    }
