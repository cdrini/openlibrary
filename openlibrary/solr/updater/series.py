from openlibrary.solr.updater.list import ListSolrBuilder, ListSolrUpdater, fetch_seeds_facets
from openlibrary.solr.utils import SolrUpdateRequest


class SeriesSolrUpdater(ListSolrUpdater):
    key_prefix = '/series/'
    thing_type = '/type/series'

    async def update_key(self, list: dict) -> tuple[SolrUpdateRequest, list[str]]:
        lst = SeriesSolrBuilder(list)
        seeds = lst.seed
        lst = SeriesSolrBuilder(list, await fetch_seeds_facets(seeds))
        doc = lst.build()
        return SolrUpdateRequest(adds=[doc]), []

class SeriesSolrBuilder(ListSolrBuilder):
    @property
    def type(self):
        return 'series'
