from __future__ import annotations

import datetime
import logging
import time
from collections.abc import Iterator

import requests
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 60
_PAGE_SLEEP_SECONDS = 1.0


class OPDSContributor(BaseModel):
    name: str


class OPDSPrice(BaseModel):
    currency: str
    value: float


class OPDSIndirectAcquisition(BaseModel):
    type: str


class OPDSLinkProperties(BaseModel):
    indirectAcquisition: list[OPDSIndirectAcquisition] = []
    price: OPDSPrice | None = None


class OPDSBuyLinkProperties(BaseModel):
    indirectAcquisition: list[OPDSIndirectAcquisition] = []
    price: OPDSPrice  # always present on a confirmed buy link


class OPDSLink[TProperties = OPDSLinkProperties | None](BaseModel):
    rel: str
    href: str
    type: str | None = None
    title: str | None = None
    properties: TProperties


class OPDSPublicationMetadata(BaseModel):
    title: str
    type: str | None = None
    identifier: str | None = None
    author: list[OPDSContributor] = []
    language: list[str] = []
    published: str | None = None
    modified: str | None = None


class OPDSFeedMetadata(BaseModel):
    title: str
    numberOfItems: int | None = None
    itemsPerPage: int | None = None
    currentPage: int | None = None


_ACQUISITION_BUY_REL = "http://opds-spec.org/acquisition/buy"


class OPDSPublication(BaseModel):
    metadata: OPDSPublicationMetadata
    links: list[OPDSLink] = []
    images: list[OPDSLink] = []

    def get_modified_datetime(self) -> datetime.datetime | None:
        """Parse ``metadata.modified`` into a tz-aware UTC datetime.

        Returns None if the field is absent. Raises ValueError if present but unparseable.
        """
        if not self.metadata.modified:
            return None
        dt = datetime.datetime.fromisoformat(self.metadata.modified)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        return dt.astimezone(datetime.UTC)

    def get_buy_link(self) -> OPDSLink[OPDSBuyLinkProperties] | None:
        """Return the first buy-acquisition link that carries a price, or None."""
        for link in self.links:
            if link.rel == _ACQUISITION_BUY_REL and link.properties and link.properties.price:
                return OPDSLink[OPDSBuyLinkProperties].model_validate(link.model_dump())
        return None

    def get_cover_url(self) -> str | None:
        return next((image.href for image in self.images if image.rel == "cover"), None)


class OPDSFeed(BaseModel):
    metadata: OPDSFeedMetadata
    links: list[OPDSLink] = []
    publications: list[OPDSPublication] = []

    def get_next_url(self) -> str | None:
        return next((link.href for link in self.links if link.rel == "next"), None)

    @classmethod
    def iter_pages(cls, start_url: str, session: requests.Session, max_pages: int | None = None) -> Iterator[OPDSFeed]:
        """Yield successive OPDS feed pages, following ``rel=next`` links."""
        url: str | None = start_url
        seen: set[str] = set()
        page_num = 0
        while url:
            if url in seen:
                logger.warning("Cycle detected in OPDS pagination at %s; stopping.", url)
                return
            seen.add(url)
            page_num += 1
            if max_pages is not None and page_num > max_pages:
                logger.info("Reached --max-pages=%s; stopping pagination.", max_pages)
                return
            logger.info("Fetching OPDS page %d: %s", page_num, url)
            resp = session.get(url, timeout=_REQUEST_TIMEOUT, headers={"Accept": "application/opds+json"})
            resp.raise_for_status()
            feed = cls.model_validate(resp.json())
            yield feed
            url = feed.get_next_url()
            if url:
                time.sleep(_PAGE_SLEEP_SECONDS)
