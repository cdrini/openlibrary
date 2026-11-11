import copy
import json
import re
from dataclasses import dataclass
from typing import Literal, cast, override

import requests
import web
from lxml import etree

from infogami.utils import delegate
from infogami.utils.flash import add_flash_message
from infogami.utils.template import render_template
from openlibrary.catalog import add_book
from openlibrary.catalog.marc.html import html_record
from openlibrary.catalog.marc.marc_binary import MarcBinary
from openlibrary.catalog.marc.marc_xml import MarcXml
from openlibrary.catalog.marc.parse import read_edition
from openlibrary.core.vendors import get_amazon_metadata_and_product
from openlibrary.utils.request_context import site


class import_preview(delegate.page):
    path = "/import/preview"
    """Preview page for import API."""

    def GET(self):
        user = site.get().get_user()

        if user is None:
            raise web.unauthorized()
        has_access = user and user.is_librarian_or_higher()
        if not has_access:
            raise web.forbidden()

        req = ImportPreviewRequest.from_input(web.input(provider="amazon", identifier=""))
        # GET requests should not save the import
        req.save = False
        return render_template("import_preview.html", metadata_provider_factory, req)

    def POST(self):
        user = site.get().get_user()

        if user is None:
            raise web.unauthorized()
        has_access = user and user.is_librarian_or_higher()
        if not has_access:
            raise web.forbidden()

        try:
            req = ImportPreviewRequest.from_input(web.input())
        except ValueError as e:
            add_flash_message("error", str(e))
            return render_template("import_preview.html", metadata_provider_factory)

        source = None
        try:
            source = req.metadata_provider.fetch(req.identifier)
            result = req.metadata_provider.load(source, req.save)
        except ValueError as e:
            add_flash_message("error", str(e))
            return render_template("import_preview.html", metadata_provider_factory, req=req, source=source)

        if req.save:
            add_flash_message("success", "Import successful")
            return web.seeother(result["edition"]["key"])
        else:
            return render_template(
                "import_preview.html",
                metadata_provider_factory,
                req=req,
                result=result,
                source=source,
            )


@dataclass
class ImportPreviewRequest:
    metadata_provider: AbstractMetadataProvider
    identifier: str
    save: bool

    @staticmethod
    def from_input(i: dict) -> ImportPreviewRequest:
        source = cast(str | None, i.get("source"))
        provider = cast(str | None, i.get("provider"))

        if not source and not provider:
            raise ValueError("No provider specified")

        identifier: str | None = None
        if source:
            if ":" not in source:
                raise ValueError("Invalid source provided")

            provider_prefix, identifier = source.split(":", 1)
        elif provider:
            provider_prefix = provider
            identifier = cast(str | None, i.get("identifier"))

        metadata_provider = metadata_provider_factory.get_metadata_provider(provider_prefix)

        if not metadata_provider:
            raise ValueError("Invalid source provided")

        return ImportPreviewRequest(
            metadata_provider=metadata_provider,
            identifier=identifier or "",
            save=i.get("save", "false") == "true",
        )


@dataclass
class SourceRecord:
    """A provider's raw metadata, and the import record built from it."""

    import_record: dict
    # The source's data before it was turned into an import record
    raw_metadata: dict | None = None
    marc_record: MarcBinary | MarcXml | None = None
    from_marc_record: bool = False

    def marc_text(self) -> str | None:
        """The MARC record as it was fetched: binary MARC, or MARC XML."""
        if isinstance(self.marc_record, MarcBinary):
            return self.marc_record.data.decode("utf-8", errors="replace")
        if isinstance(self.marc_record, MarcXml):
            return etree.tostring(self.marc_record.record, encoding="unicode", pretty_print=True)
        return None

    def marc_html_record(self) -> html_record | None:
        """Binary MARC, for rendering as on /show-records/ pages."""
        if isinstance(self.marc_record, MarcBinary):
            try:
                return html_record(self.marc_record.data)
            except AssertionError, ValueError:
                return None
        return None


class AbstractMetadataProvider:
    full_name: str
    id_name: str

    def fetch(self, identifier: str) -> SourceRecord:
        """Fetch the source's metadata and build an import record from it."""
        raise NotImplementedError(f"fetch method not implemented for {self.__class__.__name__}")

    def load(self, source: SourceRecord, save: bool) -> dict:
        return add_book.load(
            # load() modifies the record, and the caller may still show it
            copy.deepcopy(source.import_record),
            account_key="account/ImportBot",
            from_marc_record=source.from_marc_record,
            save=save,
        )

    def do_import(self, identifier: str, save: bool) -> dict:
        return self.load(self.fetch(identifier), save)


class AmazonMetadataProvider(AbstractMetadataProvider):
    full_name = "Amazon"
    id_name = "amazon"

    @override
    def fetch(self, identifier: str) -> SourceRecord:
        id_type: Literal["asin", "isbn"] = "isbn" if identifier[0].isdigit() else "asin"
        import_record, product = get_amazon_metadata_and_product(id_=identifier, id_type=id_type)
        assert import_record, "No metadata found for the given identifier"
        return SourceRecord(import_record, raw_metadata=product)


class IaMetadataProvider(AbstractMetadataProvider):
    full_name = "Internet Archive"
    id_name = "ia"

    @override
    def fetch(self, identifier: str) -> SourceRecord:
        # This appears to be causing a circular dependency... only in tests?
        from openlibrary.plugins.importapi.code import ia_importapi

        import_record, from_marc_record, metadata, marc_record = ia_importapi.get_ia_import_record(
            identifier,
            require_marc=False,
            force_import=True,
            preview=True,
        )
        assert import_record, "No metadata found for the given identifier"
        return SourceRecord(
            import_record,
            # _filenames lists the item's files, which OL adds to the item metadata
            raw_metadata={k: v for k, v in metadata.items() if k != "_filenames"},
            marc_record=marc_record,
            from_marc_record=from_marc_record,
        )


class RawMarcMetadataProvider(AbstractMetadataProvider):
    full_name = "Raw MARC"
    id_name = "marc"

    @override
    def fetch(self, identifier: str) -> SourceRecord:
        # The "ID" is a url, with a byte offset for the start of the MARC record.
        # Example: "https://archive.org/download/harvard_bibliographic_metadata/20220215_007.bib.mrc:35217689"
        url, offset = identifier.rsplit(":", 1)

        headers = {"Range": f"bytes={offset}-"}
        response = requests.get(url, headers=headers, stream=True)
        if response.status_code != 206:
            raise ValueError(f"Failed to fetch MARC record from {url}: {response.status_code}")

        # The first 5 bytes are the length of the MARC record.
        marc_data = response.raw.read(5)
        if len(marc_data) < 5:
            raise ValueError("Invalid MARC record data: less than 5 bytes received")
        length = int(marc_data[:5])
        marc_data += response.raw.read(length - 5)
        marc_record = MarcBinary(marc_data)
        import_record = read_edition(marc_record)

        if "source_records" not in import_record:
            import_record["source_records"] = [f"marc:{url}:{offset}:{length}"]

        return SourceRecord(import_record, marc_record=marc_record, from_marc_record=True)


class GutenbergMetadataProvider(AbstractMetadataProvider):
    full_name = "Project Gutenberg"
    # Matches the ``source_records`` prefix and identifier type that the
    # BookWorm Gutenberg feed writes, so a preview and a harvest agree.
    id_name = "project_gutenberg"

    PUBLICATION_URL = "https://opds-test.pglaf.org/opds/publications?id={}"
    # A bare ebook number, or its gutenberg.org page URL.
    ID_RE = re.compile(r"^(?:https?://(?:www\.)?gutenberg\.org/ebooks/)?(\d+)/?$")

    @override
    def fetch(self, identifier: str) -> SourceRecord:
        from openlibrary.bookworm import opds
        from openlibrary.bookworm.harvest import build_session

        match = self.ID_RE.match(identifier.strip())
        if not match:
            raise ValueError(f"Invalid Project Gutenberg id: {identifier!r}")

        response = build_session().get(self.PUBLICATION_URL.format(match[1]), timeout=20)
        response.raise_for_status()
        publication = response.json()
        feed = opds.Feed(provider_name=self.id_name, id_strategy="gutenberg")
        import_record = opds.to_import_record(opds.Publication(**publication), feed)
        assert import_record, "No metadata found for the given identifier"
        return SourceRecord(import_record, raw_metadata=publication)


class RawJsonMetadataProvider(AbstractMetadataProvider):
    full_name = "Raw import JSON"
    id_name = "json"

    @override
    def fetch(self, identifier: str) -> SourceRecord:
        # Avoids the same circular import as IaMetadataProvider.
        from openlibrary.plugins.importapi.code import json_edition_builder

        try:
            obj = json.loads(identifier)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("The import record must be a JSON object")
        # Built like a JSON POST to /api/import, so the preview shows what that would do.
        import_record = json_edition_builder(obj).get_dict()
        return SourceRecord(import_record, raw_metadata=obj)


class MetadataProviderFactory:
    def __init__(self):
        providers = [
            AmazonMetadataProvider(),
            IaMetadataProvider(),
            RawMarcMetadataProvider(),
            GutenbergMetadataProvider(),
            RawJsonMetadataProvider(),
        ]
        self.metadata_providers = {provider.id_name: provider for provider in providers}

    def get_metadata_provider(self, provider_name: str) -> AbstractMetadataProvider | None:
        if provider_name not in self.metadata_providers:
            return None
        return self.metadata_providers[provider_name]


metadata_provider_factory = MetadataProviderFactory()


def setup():
    pass


setup()
