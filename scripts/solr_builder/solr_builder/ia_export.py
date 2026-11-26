"""
Export the archive.org metadata the solr builder needs for every OL-linked IA item,
using the IA Scrape API, into a gzipped `identifier<TAB>json` file that
`psql-import-simple.sh` can load into the `ia_metadata` table.

Docs have the same raw shape as the advancedsearch docs that
`DataProvider._get_lite_metadata` caches (same fields, same single/multi-valued forms).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

from openlibrary.solr.data_provider import IA_METADATA_FIELDS

logger = logging.getLogger("openlibrary.solr-builder.ia-export")

SCRAPE_URL = "https://archive.org/services/search/v1/scrape"
# The scrape API silently ignores `q` for some page sizes (e.g. count=100 returns an
# unrelated item), so only the max page size is trusted.
PAGE_SIZE = 10000


class ScrapePageError(Exception):
    pass


def validate_page(page: dict, count: int) -> list[dict]:
    items = page.get("items")
    if not isinstance(items, list):
        raise ScrapePageError(f"Page has no items list: keys={sorted(page)}")
    if page.get("count") != len(items):
        raise ScrapePageError(f"Page count {page.get('count')} != {len(items)} items")
    if len(items) > count:
        raise ScrapePageError(f"Page has {len(items)} items, more than requested {count}")
    for item in items:
        identifier = item.get("identifier")
        if not isinstance(identifier, str) or not identifier or any(c in identifier for c in "\t\r\n"):
            raise ScrapePageError(f"Bad identifier in page: {item!r}")
    if "total" in page and not isinstance(page["total"], int):
        raise ScrapePageError(f"Bad total: {page['total']!r}")
    return items


def fetch_page(
    client: httpx.Client,
    params: dict,
    retries: int,
    backoff: float,
) -> dict:
    for attempt in range(retries + 1):
        try:
            r = client.get(SCRAPE_URL, params=params)
            r.raise_for_status()
            page = r.json()
            validate_page(page, params["count"])
            return page
        except (httpx.HTTPError, ValueError, ScrapePageError) as e:
            if attempt == retries:
                raise
            delay = backoff * 2**attempt
            logger.warning("Scrape page failed (attempt %d/%d), retrying in %.0fs: %r", attempt + 1, retries + 1, delay, e)
            time.sleep(delay)
    raise AssertionError("unreachable")


def scrape(
    client: httpx.Client,
    query: str,
    scope: str | None = None,
    count: int = PAGE_SIZE,
    sleep: float = 1.0,
    retries: int = 5,
    backoff: float = 10.0,
    max_pages: int | None = None,
) -> Iterator[tuple[dict, list[dict]]]:
    """Yields (page, items) for each page of the scrape, following cursors."""
    params: dict = {
        "q": query,
        "fields": ",".join(IA_METADATA_FIELDS),
        "count": count,
    }
    if scope:
        params["scope"] = scope

    pages = 0
    while True:
        page = fetch_page(client, params, retries, backoff)
        pages += 1
        yield page, page["items"]

        cursor = page.get("cursor")
        if not cursor or (max_pages is not None and pages >= max_pages):
            return
        params["cursor"] = cursor
        time.sleep(sleep)


def main(
    output: Path,
    query: str = "openlibrary_edition:*",
    scope: str | None = None,
    sleep: float = 1.0,
    timeout: float = 120.0,
    retries: int = 5,
    backoff: float = 10.0,
    max_pages: int | None = None,
    tolerance: float = 0.01,
    log_file: str | None = None,
) -> None:
    """
    :param output: Where to write the gzipped `identifier<TAB>json` file. Written to
        `<output>.partial` first and only moved into place if the scrape finishes.
    :param query: IA search query selecting the items to export
    :param scope: Scrape API scope; `all` includes noindex items but needs an authorized
        account, passed via the IA_ACCESS_KEY / IA_SECRET_KEY env vars (S3 keys).
    :param sleep: Seconds to wait between pages
    :param timeout: Per-request timeout in seconds
    :param retries: Retries per page on HTTP errors or malformed pages
    :param backoff: Base delay in seconds for exponential backoff between retries
    :param max_pages: Stop after this many pages (for testing)
    :param tolerance: Max allowed relative difference between items written and the
        reported total before exiting non-zero
    :param log_file: Redirect logs to file instead of stderr
    """
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    headers = {"x-application-id": "ol-solr"}
    if os.environ.get("IA_ACCESS_KEY") and os.environ.get("IA_SECRET_KEY"):
        headers["authorization"] = f"LOW {os.environ['IA_ACCESS_KEY']}:{os.environ['IA_SECRET_KEY']}"

    output = Path(output)
    partial = output.with_name(output.name + ".partial")
    total: int | None = None
    written = 0
    pages = 0
    start = time.time()
    logger.info("Starting IA scrape export: q=%r scope=%r -> %s", query, scope, output)

    with httpx.Client(headers=headers, timeout=timeout) as client, gzip.open(partial, "wt", encoding="utf-8") as f:
        for page, items in scrape(
            client,
            query,
            scope=scope,
            sleep=sleep,
            retries=retries,
            backoff=backoff,
            max_pages=max_pages,
        ):
            if total is None:
                total = page.get("total")
            for item in items:
                f.write(item["identifier"])
                f.write("\t")
                f.write(json.dumps(item))
                f.write("\n")
            written += len(items)
            pages += 1
            if pages % 10 == 0 or not page.get("cursor"):
                logger.info("Page %d: %d/%s items (%.0fs)", pages, written, total, time.time() - start)

    partial.replace(output)
    logger.info("Wrote %d items in %d pages to %s (%.0fs)", written, pages, output, time.time() - start)

    if max_pages is None and total:
        shortfall = abs(total - written) / total
        if shortfall > tolerance:
            logger.error("Exported %d items but scrape reported total %d (%.1f%% off)", written, total, 100 * shortfall)
            raise SystemExit(1)


if __name__ == "__main__":
    from scripts.solr_builder.solr_builder.fn_to_cli import FnToCLI

    FnToCLI(main).run()
