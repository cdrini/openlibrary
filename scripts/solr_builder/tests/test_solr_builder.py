import re

import pytest

from scripts.solr_builder.solr_builder.solr_builder import LocalPostgresDataProvider


@pytest.fixture
def provider(tmp_path, monkeypatch):
    conf = tmp_path / "db.ini"
    conf.write_text("[postgres]\nhost=localhost\n")
    provider = LocalPostgresDataProvider(str(conf))
    rows = {"/authors/OL1A": {"key": "/authors/OL1A", "type": {"key": "/type/author"}}}
    monkeypatch.setattr(
        provider,
        "query_iter",
        lambda q: iter([(doc,) for key, doc in rows.items() if f"'{key}'" in q]),
    )
    return provider


class TestLocalPostgresDataProvider:
    @pytest.mark.asyncio
    async def test_get_document(self, provider):
        assert await provider.get_document("/authors/OL1A") == {
            "key": "/authors/OL1A",
            "type": {"key": "/type/author"},
        }

    @pytest.mark.asyncio
    async def test_get_document_missing(self, provider):
        assert await provider.get_document("/authors/OL2A") == {
            "key": "/authors/OL2A",
            "type": {"key": "/type/delete"},
        }


class FakeLocalPostgresDataProvider(LocalPostgresDataProvider):
    def __init__(self, docs: list[dict]):
        self.cache = {}
        self.docs_by_key = {doc["key"]: doc for doc in docs}
        self.queries: list[str] = []

    def query_all(self, query: str, json_cache: dict | None = None) -> list:
        self.queries.append(query)
        keys = re.findall(r"'([^']+)'", query)
        rows = [(k, self.docs_by_key[k]) for k in keys if k in self.docs_by_key]
        if json_cache is not None:
            json_cache.update(dict(rows))
        return rows


class TestCacheRedirectTargets:
    def test_follows_redirect_chains_one_query_per_hop(self):
        docs = [
            {"key": "/authors/OL1A", "type": {"key": "/type/redirect"}, "location": "/authors/OL2A"},
            {"key": "/authors/OL2A", "type": {"key": "/type/redirect"}, "location": "/authors/OL3A"},
            {"key": "/authors/OL3A", "type": {"key": "/type/author"}},
            {"key": "/authors/OL4A", "type": {"key": "/type/redirect"}, "location": "/authors/OL5A"},
            {"key": "/authors/OL5A", "type": {"key": "/type/author"}},
            {"key": "/authors/OL6A", "type": {"key": "/type/author"}},
        ]
        dp = FakeLocalPostgresDataProvider(docs)
        rows = [(k, dp.docs_by_key[k]) for k in ("/authors/OL1A", "/authors/OL4A", "/authors/OL6A")]
        dp.cache.update(dict(rows))

        dp.cache_redirect_targets(rows)

        assert len(dp.queries) == 2
        assert {"/authors/OL2A", "/authors/OL3A", "/authors/OL5A"} <= dp.cache.keys()

    def test_no_query_without_redirects(self):
        dp = FakeLocalPostgresDataProvider([])
        dp.cache_redirect_targets([("/authors/OL1A", {"key": "/authors/OL1A", "type": {"key": "/type/author"}})])
        assert dp.queries == []

    def test_missing_target_cached_as_deleted(self):
        redirect = {"key": "/authors/OL1A", "type": {"key": "/type/redirect"}, "location": "/authors/OL2A"}
        dp = FakeLocalPostgresDataProvider([redirect])
        dp.cache_redirect_targets([(redirect["key"], redirect)])
        assert dp.cache["/authors/OL2A"]["type"]["key"] == "/type/delete"
