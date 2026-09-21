import pytest

from openlibrary.solr.updater.edition import EditionSolrBuilder, EditionSolrUpdater, sort_title
from openlibrary.tests.solr.test_update import FakeDataProvider, make_edition


class TestEditionSolrUpdater:
    @pytest.mark.asyncio
    async def test_deletes_old_orphans(self):
        req, new_keys = await EditionSolrUpdater(FakeDataProvider()).update_key(
            {
                "key": "/books/OL1M",
                "type": {"key": "/type/edition"},
                "works": [{"key": "/works/OL1W"}],
            }
        )

        assert req.deletes == ["/works/OL1M"]
        assert req.adds == []
        assert new_keys == ["/works/OL1W"]

    @pytest.mark.asyncio
    async def test_enqueues_orphans_as_works(self):
        req, new_keys = await EditionSolrUpdater(FakeDataProvider()).update_key({"key": "/books/OL1M", "type": {"key": "/type/edition"}})

        assert req.deletes == []
        assert req.adds == []
        assert new_keys == ["/works/OL1M"]


@pytest.mark.parametrize(
    ("title", "subtitle", "expected"),
    [
        ("The Great Gatsby", None, "Great Gatsby, The"),
        ("Dune", None, "Dune"),
        ("The Hobbit", "There and Back Again", "Hobbit: There and Back Again, The"),
        ("L'amour", None, "amour, L'"),
    ],
)
def test_sort_title(title, subtitle, expected):
    assert sort_title(title, subtitle) == expected


class TestEditionSolrBuilder:
    def test_chapter(self):
        edition = make_edition(
            key="/books/OL1M",
            table_of_contents=[
                {"level": 1, "label": "Chapter 1", "title": "Beginnings", "pagenum": "3"},
                {"level": 1, "title": "Middles", "subtitle": "The muddle", "authors": [{"name": "Alice Author"}]},
                # Legacy shapes that predate /type/toc_item
                "A plain string chapter",
                {"type": "/type/text", "value": "A /type/text chapter"},
                {"level": "2", "title": "A chapter with a string level"},
            ],
        )

        assert EditionSolrBuilder(edition, solr_work={}, db_work=None, db_authors=[]).chapter == [
            "OL1M | Chapter 1 | Beginnings | 3",
            "OL1M |  | Middles: The muddle (Alice Author) | ",
            "OL1M |  | A plain string chapter | ",
            "OL1M |  | A /type/text chapter | ",
            "OL1M |  | A chapter with a string level | ",
        ]

    def test_identifiers(self):
        edition = make_edition(
            identifiers={
                "Some.Weird.Key##": ["  id-1  ", None, "id-1", "id-2  "],
                "foo": [None],
            }
        )

        assert EditionSolrBuilder(edition, solr_work={}, db_work=None, db_authors=[])._identifiers == {
            "id_some_weird_key": ["id-1", "id-2"],
        }
