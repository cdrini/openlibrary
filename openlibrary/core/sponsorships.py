from datetime import datetime, timedelta
import json
import time

import requests
import logging
import web

from six.moves.urllib.parse import urlencode

from collections import OrderedDict
from infogami.utils.view import public
from openlibrary.core import lending
from openlibrary.core.vendors import (
    get_betterworldbooks_metadata,
    get_amazon_metadata)
from openlibrary.accounts.model import get_internet_archive_id
from openlibrary.core.civicrm import (
    get_contact_id_by_username,
    get_sponsorships_by_contact_id)
from openlibrary.plugins.worksearch.search import get_solr
from openlibrary.utils.dateutil import parse_epoch_timestamp_ms, get_epoch_timestamp_ms

try:
    from booklending_utils.sponsorship import eligibility_check
except ImportError:
    def eligibility_check(edition):
        """For testing if Internet Archive book sponsorship check unavailable"""
        return False

logger = logging.getLogger("openlibrary.sponsorship")
SETUP_COST_CENTS = 300
PAGE_COST_CENTS = 12
SOLR_EXPIRATION = timedelta(days=1)
solr = get_solr()


def get_sponsored_editions(user):
    """
    Gets a list of books from the civi API which internet archive
    @archive_username has sponsored

    :param user user: infogami user
    :rtype: list
    :return: list of editions sponsored by user
    """
    archive_id = get_internet_archive_id(user.key if 'key' in user else user._key)
    contact_id = get_contact_id_by_username(archive_id)
    return get_sponsorships_by_contact_id(contact_id) if contact_id else []

def get_sponsorable_editions():
    """This should move to an infogami type so any admin can add editions
    to the list. This will need to be paginated.
    """
    try:
        candidates = web.ctx.site.get('/sponsorship/books').get('editions', [])
    except AttributeError:
        candidates = []

    eligible_editions = []
    for key in candidates:
        ed = web.ctx.site.get(key)
        ed.eligibility = qualifies_for_sponsorship(ed)
        if ed.eligibility.get('is_eligible'):
            eligible_editions.append(ed)
    return eligible_editions


def do_we_want_it(isbn, work_id):
    """
    Returns True if we don't have this edition (or other editions of
    the same work), if the isbn has not been promised to us, has not
    yet been sponsored, and is not already in our possession.

    :param str isbn: isbn10 or isbn13
    :param str work_id: e.g. OL123W
    :rtype: (bool, list)
    :return: bool answer to do-we-want-it, list of matching books
    """
    availability = lending.get_work_availability(work_id)  # checks all editions
    if availability and availability.get(work_id, {}).get('status', 'error') != 'error':
        return False, availability

    # We don't have any of these work's editions available to borrow
    # Let's confirm this edition hasn't already been sponsored or promised
    params = {
        'search_field': 'isbn',
        'include_promises': 'true',  # include promises and sponsored books
        'search_id': isbn
    }
    url = '%s/book/marc/ol_dedupe.php' % lending.config_ia_domain
    r = requests.get(url, params=params)
    try:
        data = r.json()
        dwwi = data.get('response', 0)
        return dwwi==1, data.get('books', [])
    except:
        logger.error("DWWI Failed for isbn %s" % isbn, exc_info=True)
    # err on the side of false negative
    return False, []

@public
def qualifies_for_sponsorship(edition):
    """
    :param openlibrary.plugins.upstream.models.Edition edition:
    :rtype: dict
    :return: A dict with book eligibility:
    {
     "edition": {
        "publishers": ["University of Wisconsin Press"],
        "number_of_pages": 262,
        "publish_date": "September 22, 2004",
        "cover": "https://covers.openlibrary.org/b/id/2353907-L.jpg",
        "title": "Lords of the Ring",
        "isbn": "9780299204204"
        "openlibrary_edition": "OL2347684M",
        "openlibrary_work": "OL10317216W"
       },
       "price": {
          "scan_price_cents": 3444,
          "book_cost_cents": 298,
          "total_price_display": "$37.42",
          "total_price_cents": 3742
        },
       "is_eligible": True,
       "sponsor_url": "https://archive.org/donate?isbn=9780299204204&type=sponsorship&context=ol&campaign=pilot"
    }
    """
    resp = {
        'is_eligible': False,
        'price': None
    }

    # TODO: Add these as @property to Edition class; don't modify the type here
    edition.isbn = edition.get_isbn13()
    edition.cover = edition.get('covers') and (
        'https://covers.openlibrary.org/b/id/%s-L.jpg' % edition.covers[0])
    amz_metadata = edition.isbn and get_amazon_metadata(edition.isbn) or {}
    req_fields = ['isbn', 'publishers', 'title', 'publish_date', 'cover', 'number_of_pages']
    edition_data = dict((field, (amz_metadata.get(field) or edition.get(field))) for field in req_fields)
    work = edition.works and edition.works[0]

    if not work or not all(edition_data.values()):
        resp['error'] = {
            'reason': 'Open Library is missing book metadata necessary for sponsorship',
            'values': edition_data
        }
        return resp

    work_id = work.key.split("/")[-1]
    edition_id = edition.key.split('/')[-1]
    dwwi, matches = do_we_want_it(edition.isbn, work_id)
    if dwwi:
        bwb_price = get_betterworldbooks_metadata(edition.isbn, thirdparty=True).get('price_amt')
        if bwb_price:
            num_pages = int(edition_data['number_of_pages'])
            scan_price_cents = SETUP_COST_CENTS + (PAGE_COST_CENTS * num_pages)
            book_cost_cents = int(float(bwb_price) * 100)
            total_price_cents = scan_price_cents + book_cost_cents
            resp['price'] = {
                'book_cost_cents': book_cost_cents,
                'scan_price_cents': scan_price_cents,
                'total_price_cents': total_price_cents,
                'total_price_display': '${:,.2f}'.format(
                    total_price_cents / 100.
                ),
            }
            resp['is_eligible'] = eligibility_check(edition)
    else:
        resp['error'] = {
            'reason': 'matches',
            'values': matches
        }
    edition_data.update({
        'openlibrary_edition': edition_id,
        'openlibrary_work': work_id
    })
    resp.update({
        'edition': edition_data,
        'sponsor_url': lending.config_ia_domain + '/donate?' + urlencode({
            'campaign': 'pilot',
            'type': 'sponsorship',
            'context': 'ol',
            'isbn': edition.isbn
        })
    })
    price = resp.get('price', {}).get('total_price_cents')
    # TODO: Do this non-blocking. Don't need response.
    index_sponsorship_status(work.key, edition.key, price)
    return resp


def index_sponsorship_status(work_key, edition_key, price):
    """
    :param str work_key: e.g. /works/OL1W
    :param str edition_key: e.g. /books/OL1M
    :param int|None price: price in cents; None if not sponsorable
    """
    solr_doc = solr.get_doc(work_key)
    if not solr_doc:
        return
    sponsorship_data = json.loads(solr_doc.get('sponsorship_data_s', '{}'))
    changed = False

    if price is None and edition_key in sponsorship_data:
        del sponsorship_data[edition_key]
        changed = True
    else:
        prev_data = sponsorship_data.get(edition_key, {})

        def expired():
            prev = parse_epoch_timestamp_ms(sponsorship_data[edition_key]['timestamp'])
            return (datetime.now() - prev) > SOLR_EXPIRATION

        # Only perform the update if price doesn't match
        if price != prev_data.get('price') or expired():
            sponsorship_data[edition_key] = {
                'price': price,
                'timestamp': get_epoch_timestamp_ms(),
            }

            changed = True

    if changed:
        if sponsorship_data:
            solr_doc.update({
                'sponsorship_data_s': json.dumps(sponsorship_data),
                'min_sponsorship_price_i': min(*[d['price'] for d in sponsorship_data]),
            })
        else:
            del solr_doc['sponsorship_data_s']
            del solr_doc['min_sponsorship_price_i']

        solr.update_doc(solr_doc)


def get_sponsored_books():
    """Performs the `ia` query to fetch sponsored books from archive.org"""
    from internetarchive import search_items
    params = {'page': 1, 'rows': 1000, 'scope': 'all'}
    fields = ['identifier','est_book_price','est_scan_price', 'scan_price',
              'book_price', 'repub_state', 'imagecount', 'title',
              'openlibrary_edition']

    q = 'collection:openlibraryscanningteam'

    # XXX Note: This `search_items` query requires the `ia` tool (the
    # one installed via virtualenv) to be configured with (scope:all)
    # privileged s3 keys.
    config = {'general': {'secure': False}}
    return search_items(q, fields=fields, params=params, config=config)


def summary():
    """
    Provides data model (finances, state-of-process) for the /admin/sponsorship stats
    page.

    Screenshot:
    https://user-images.githubusercontent.com/978325/71494377-b975c880-27fb-11ea-9c95-c0c1bfa78bda.png
    """
    items = list(get_sponsored_books())

    # Construct a map of each state of the process to a count of books in that state
    STATUSES = ['Needs purchasing', 'Needs digitizing', 'Needs republishing', 'Complete']
    status_counts = OrderedDict((status, 0) for status in STATUSES)
    for book in items:
        # Official sponsored items set repub_state -1 at item creation,
        # bulk sponsorship (item created at time of scanning by ttscribe)
        # will not be repub_state -1. Thus. books which have repub_state -1
        # and no price are those we need to digitize:
        if int(book.get('repub_state', -1)) == -1 and not book.get('book_price'):
            book['status'] = STATUSES[0]
            status_counts[STATUSES[0]] += 1
        elif int(book.get('repub_state', -1)) == -1:
            book['status'] = STATUSES[1]
            status_counts[STATUSES[1]] += 1
        elif int(book.get('repub_state', 0)) < 14:
            book['status'] = STATUSES[2]
            status_counts[STATUSES[2]] += 1
        else:
            book['status'] = STATUSES[3]
            status_counts[STATUSES[3]] += 1

    total_pages_scanned = sum(int(i.get('imagecount', 0)) for i in items)
    total_unscanned_books = len([i for i in items if not i.get('imagecount', 0)])
    total_cost_cents = sum(int(i.get('est_book_price', 0)) + int(i.get('est_scan_price', 0))
                           for i in items)
    book_cost_cents = sum(int(i.get('book_price', 0)) for i in items)
    est_book_cost_cents = sum(int(i.get('est_book_price', 0)) for i in items)
    scan_cost_cents = (PAGE_COST_CENTS * total_pages_scanned) + (SETUP_COST_CENTS * len(items))
    est_scan_cost_cents = sum(int(i.get('est_scan_price', 0)) for i in items)
    avg_scan_cost = scan_cost_cents / (len(items) - total_unscanned_books) 

    return {
        'books': items,
        'status_ids': dict((name, i) for i, name in enumerate(STATUSES)),
        'status_counts': status_counts,
        'total_pages_scanned': total_pages_scanned,
        'total_unscanned_books': total_unscanned_books,
        'total_cost_cents': total_cost_cents,
        'book_cost_cents': book_cost_cents,
        'est_book_cost_cents': est_book_cost_cents,
        'delta_book_cost_cents': est_book_cost_cents - book_cost_cents,
        'scan_cost_cents': scan_cost_cents,
        'est_scan_cost_cents': est_scan_cost_cents,
        'delta_scan_cost_cents': est_scan_cost_cents - scan_cost_cents,
        'avg_scan_cost': avg_scan_cost,
    }
