"""
Reads JSON objects on stdin and inserts them into Solr.
"""
import sys
import json
import logging
import re
from unicodedata import normalize

from lxml.etree import Element, tostring

from openlibrary.data.solr import re_year
from openlibrary.solr.update_work import datetimestr_to_int, UpdateRequest
from openlibrary.utils import str_to_key, uniq
from openlibrary.utils.isbn import opposite_isbn
from openlibrary.utils.solr import Solr

logger = logging.getLogger("openlibrary.solr-injector")


re_bad_char = re.compile('[\x01\x0b\x1a-\x1e]')
def strip_bad_char(s):
    if not isinstance(s, basestring):
        return s
    return re_bad_char.sub('', s)


def add_field(doc, name, value):
    """
    Add an XML element to the provided doc of the form `<field name="$name">$value</field>`.

    Example:

    >>> tostring(add_field(Element("doc"), "foo", "bar"))
    "<doc><field name="foo">bar</field></doc>"

    :param lxml.etree.ElementBase doc: Parent document to append to.
    :param str name:
    :param value:
    """
    field = Element("field", name=name)
    try:
        field.text = normalize('NFC', unicode(strip_bad_char(value)))
    except:
        print('Error in normalizing %r', value)
        logger.error('Error in normalizing %r', value)
        raise
    doc.append(field)

def add_field_list(doc, name, field_list):
    """
    Add multiple XML elements to the provided element of the form `<field name="$name">$value[i]</field>`.

    Example:

    >>> tostring(add_field_list(Element("doc"), "foo", [1,2]))
    "<doc><field name="foo">1</field><field name="foo">2</field></doc>"

    :param lxml.etree.ElementBase doc: Parent document to append to.
    :param str name:
    :param list field_list:
    """
    for value in field_list:
        add_field(doc, name, value)

def dict2element(d):
    """
    Convert the dict to insert into Solr into Solr XML <doc>.

    :param dict d:
    :rtype: lxml.etree.ElementBase
    """
    doc = Element("doc")
    for k, v in d.items():
        if isinstance(v, (list, set)):
            add_field_list(doc, k, v)
        else:
            add_field(doc, k, v)
    return doc




def insert_author(author):
    """
    :param dict author: author object to insert
    :return: Solr document as a dict
    :rtype: dict
    """
    solr_doc = dict(
        key=author['key'],
        type='author',
    )

    for f in 'name', 'alternate_names', 'birth_date', 'death_date', 'date':
        if f in author:
            solr_doc[f] = author[f]

    # TODO: needs data from works
    # - [x] top_work
    # - [x] work_count
    # - [x] top_subjects

    return solr_doc


def update_author_w_works(solr, author_doc):
    facet_fields = ['subject', 'time', 'person', 'place']

    resp = solr.select(
        query=dict(author_key=author_doc['key']),
        fields=['title', 'subtitle'],
        facets=[f + '_facet' for f in facet_fields],
        facet_mincount=1,
        sort='edition_count desc',
        rows=1)

    author_doc['work_count'] = resp.num_found
    if resp.docs:
        top_work = resp.docs[0]
        author_doc['top_work'] = top_work['title']
        if 'subtitle' in top_work:
            author_doc['top_work'] += ': ' + top_work['subtitle']

        all_subjects = sorted(resp.facets.itervalues(), reverse=True, key=lambda x: x.count)
        author_doc['top_subjects'] = [x.value for x in all_subjects[:10]]


def insert_edition_as_work(edition):
    short_key = edition['key'].split('/')[2]
    solr_doc = insert_work(edition)
    solr_doc['key'] = '/works/%s' % short_key
    return solr_doc


def insert_work(work):
    """
    :param dict work: work object to insert
    :return: Solr document as a dict
    :rtype: dict
    """
    solr_doc = dict(
        key=work['key'],
        type='work',
        last_modified_i=datetimestr_to_int(work['last_modified'])
    )

    for field in 'title', 'subtitle':
        if field in work:
            solr_doc[field] = work[field]

    if 'covers' in work:
        solr_doc['cover_i'] = work['covers'][0]

    if 'authors' in work:
        # FIXME: Related to #1059 ; shouldn't need the if check
        solr_doc['author_key'] = [a['author']['key'] for a in work.get('authors', []) if 'author' in a]

    # map openlibrary object fields to solr document fields
    subject_fields_map = {
        'subjects': 'subject',
        'subject_people': 'person',
        'subject_places': 'place',
        'subject_times': 'time',
    }

    for ol_field, solr_field in subject_fields_map.iteritems():
        if ol_field in work:
            solr_doc[solr_field] = work[ol_field]
            solr_doc[solr_field + '_facet'] = work[ol_field]
            solr_doc[solr_field + '_key'] = [str_to_key(s) for s in work[ol_field]]

    solr_doc['seed'] = [work['key']]

    # Add subjects to seed
    for field in subject_fields_map.itervalues():
        if field + '_key' in solr_doc:
            prefix = "/subjects/"
            if field != "subject":
                prefix += field + ":"
            solr_doc['seed'] += [prefix + s for s in solr_doc[field + '_key']]

    # Add authors to seed
    solr_doc['seed'] += solr_doc.get('author_key', [])

    solr_doc['has_fulltext'] = False
    solr_doc['edition_count'] = 0
    solr_doc['ebook_count_i'] = 0
    solr_doc['public_scan_b'] = False

    # TODO: needs data from editions
    # - [x] seed
    # - [x] has_fulltext
    # - [x] alternative_title
    # - [x] alternative_subtitle
    # - [x] edition_count
    # - [x] by_statement
    # - [x] publish_date, publish_year, first_publish_year
    # - [x] lccn, publish_place, oclc, contributor
    # - [x] isbn
    # - [x] last_modified_i
    # - [x] ebook_count_i, ia, public_scan_b, ia_collection_s, lending_edition_s, lending_identifier_s,
    #       lending_edition_s, lending_identifier_s, printdisabled_s
    # - [~] cover_edition_key
    # - [x] first_sentence
    # - [x] publisher
    # - [x] language
    # - [x] id_*
    # - [x] ia_loaded_id
    # - [x] ia_box_id

    # TODO: needs data from authors
    # - author_name
    # - author_alternative_name
    # - author_facet

    return solr_doc


def update_work_w_editions(work_doc, editions):
    for edition in editions:
        update_work_w_edition(work_doc, edition)

    def pick_cover(w_cover, editions):
        """
        Get edition that's used as the cover of the work. Otherwise get the
        first English edition, or otherwise any edition.

        :param int or None w_cover:
        :param list[dict] editions:
        :return: Edition key (ex: "/books/OL1M")
        :rtype: str or None
        """
        first_with_cover = None
        for e in filter(lambda e: 'covers' in e, editions):
            if e['covers'][0] == w_cover:
                return e['key']
            if not first_with_cover:
                first_with_cover = e['key']
            if '/languages/eng' in e.get('languages', []):
                return e['key']
        return first_with_cover

    # cover_edition is more complex
    cover_edition = pick_cover(work_doc.get('cover_i', None), editions)
    if cover_edition:
        work_doc['cover_edition_key'] = cover_edition.split("/")[2]


def update_work_w_authors(solr, work_doc):
    if 'author' not in work_doc:
        return
    resp = solr.select(
        query=' OR '.join(['author_key:' + key for key in work_doc['author_key']]),
        fields=['name', 'alternative_name']
    )

    work_doc['author_name'] = [a.name for a in resp.docs]
    work_doc['author_alternative_name'] = [n for a in resp.docs for n in a.alternative_name]
    work_doc['author_facet'] = [' '.join(v) for v in zip(work_doc['author_key'], work_doc['author_name'])]


def update_work_w_edition(solr_doc, edition):
    shortkey = edition['key'].split("/")[2]

    title_field_map = {
        'title': 'alternative_title',
        'subtitle': 'alternative_subtitle',
    }
    for ol_field, solr_field in title_field_map.iteritems():
        if ol_field in edition and edition[ol_field] not in set([solr_doc.get(ol_field, '')] + solr_doc.get(solr_field, [])):
            solr_doc[solr_field] = solr_doc.get(solr_field, []) + [edition[ol_field]]

    solr_doc['edition_count'] += 1

    if 'by_statement' in edition:
        solr_doc['by_statement'] = uniq(solr_doc.get('by_statement', []) + [edition['by_statement']])

    if 'publish_date' in edition:
        solr_doc['publish_date'] = uniq(solr_doc.get('publish_date', []) + [edition['publish_date']])
        m = re_year.search(edition['publish_date'])
        if m:
            year = int(m.group(1))
            solr_doc['publish_year'] = uniq(solr_doc.get('publish_year', []) + [year])
            solr_doc['first_publish_year'] = min(solr_doc['publish_year'])

    field_map = {
        'lccn': 'lccn',
        'publish_places': 'publish_place',
        'oclc_numbers': 'oclc',
        'contributions': 'contributor',
    }
    for ol_field, solr_field in field_map.iteritems():
        if ol_field in edition:
            solr_doc[solr_field] = uniq(solr_doc.get(solr_field, []) + edition[ol_field])

    if 'isbn_10' in edition or 'isbn_13' in edition:
        new_isbns = get_isbns(edition)
        old_isbns = set(solr_doc.get('isbns', []))
        solr_doc['isbns'] = list(old_isbns.union(new_isbns))

    last_modified_i = datetimestr_to_int(edition['last_modified'])
    if last_modified_i > solr_doc['last_modified_i']:
        solr_doc['last_modified_i'] = last_modified_i

    if 'ocaid' in edition:
        solr_doc['has_fulltext'] = True
        solr_doc['ebook_count_i'] += 1
        solr_doc['ia'] = uniq(solr_doc.get('ia', []) + edition['ocaid'])

        solr_doc['public_scan_b'] = solr_doc['public_scan_b'] or 'public_scan' in edition

        ia_collection = set()
        if 'ia_collection' in edition:
            ia_collection = set(edition['ia_collection'])
            ia_collection_s = uniq(solr_doc.get('ia_collection_s', '').split(';') + edition['ia_collection'])
            solr_doc['ia_collection_s'] = ';'.join(ia_collection_s)

        # Arbitrarily choose the first one as the lending edition?!?
        if 'lending_edition' not in solr_doc and 'lendinglibrary' in ia_collection or 'inlibrary' in ia_collection:
            solr_doc['lending_edition_s'] = shortkey
            solr_doc['lending_identifier_s'] = edition['ocaid']

        if 'printdisabled' in ia_collection:
            if 'printdisabled_s' in solr_doc:
                solr_doc['printdisabled_s'] += ';'
            else:
                solr_doc['printdisabled_s'] = ''
            solr_doc['printdisabled_s'] += shortkey

    if 'first_sentence' in edition:
        first_sentence = edition['first_sentence']
        if isinstance(first_sentence, dict):
            first_sentence = first_sentence['value']
        solr_doc['first_sentence'] = uniq(solr_doc.get('first_sentence', []) + [first_sentence])

    if 'publishers' in edition:
        publishers = ['Sine nomine' if is_sine_nomine(p) else p for p in edition['publishers']]
        solr_doc['publisher'] = uniq(solr_doc.get('publisher', []) + publishers)

    if 'languages' in edition:
        languages = [l['key'] if isinstance(l, dict) else l for l in edition['languages']]
        lang_keys = [l.split("/")[2] for l in languages]
        solr_doc['language'] = uniq(solr_doc.get('language', []) + lang_keys)

    if 'identifiers' in edition:
        for id_name, ids in edition['identifiers'].iteritems():
            solr_field = validate_solr_field('id_' + id_name)
            if solr_field:
                ids = [id.strip() for id in ids]
                solr_doc[solr_field] = uniq(solr_doc.get(solr_field, []) + ids)
            else:
                print('Bad identifier name: %s for %s', id_name, shortkey)
                logger.error('Bad identifier name: %s for %s', id_name, shortkey)

    for field in 'ia_loaded_id', 'ia_box_id':
        if field in edition:
            list_or_str = edition[field]
            if isinstance(list_or_str, basestring):
                list_or_str = [list_or_str]
            elif isinstance(list_or_str, list) and isinstance(list_or_str[0], basestring):
                pass
            else:
                print("UnexpectedType: %s for %s", field, shortkey)
                logger.error("UnexpectedType: %s for %s", field, shortkey)
                list_or_str = []

            if list_or_str:
                solr_doc[field] = uniq(solr_doc.get(field, []) + list_or_str)

    solr_doc['seed'] += [edition['key']]


re_solr_field_underscore = re.compile('[.,:]')
re_solr_field_remove = re.compile('[()/#]')
re_solr_field_test = re.compile('^[-\w]+$', re.U)
def validate_solr_field(field):
    field = re_solr_field_underscore.sub('_', field)
    field = re_solr_field_remove.sub('', field)
    return field if re_solr_field_test.match(field) else None


re_not_az = re.compile('[^a-zA-Z]')
def is_sine_nomine(pub):
    """
    Check if the publisher is 'sn' (excluding non-letter characters).

    :param str pub:
    :rtype: bool
    """
    return re_not_az.sub('', pub).lower() == 'sn'

def get_isbns(edition):
    """
    Get all ISBNs of the given edition. Calculates complementary ISBN13 for each ISBN10 and vice-versa.
    Does not remove '-'s.

    :param dict edition:
    :rtype: set[str]
    """
    isbns = set()

    isbns.update(v.replace("_", "").strip() for v in edition.get("isbn_10", []))
    isbns.update(v.replace("_", "").strip() for v in edition.get("isbn_13", []))

    # Get the isbn13 when isbn10 is present and vice-versa.
    alt_isbns = [opposite_isbn(v) for v in isbns]
    isbns.update(v for v in alt_isbns if v is not None)

    return isbns


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(
        description="Read JSON objects on stdin and insert them into Solr.")
    parser.add_argument('action', choices=['insert', 'update'],
                        help='insert: used for adding items with little knowledge of anything else'
                             'update: used for update the items; run once all have been inserted')
    parser.add_argument('--solr-endpoint', help="Location of Solr", default="http://localhost:8983")
    args = parser.parse_args()

    solr = Solr(args.solr_endpoint)

    # maintain a connection for improved performance
    solr.connect()


    def solr_update(requests):
        try:
            solr.update(requests, commit_within=5 * 60 * 1000)
        except ValueError:
            # do each one manually
            for req in requests:
                try:
                    solr.update([req], commit_within=5 * 60 * 1000)
                except ValueError:
                    print("ValueError for key: %s; skipping" % req.doc['key'])

    chunk = []
    for line in sys.stdin:
        thing = json.loads(line.strip())
        solr_docs = []
        try:
            if thing['key'].startswith('/authors/'):
                solr_doc = insert_author(thing)
                solr_docs += [solr_doc]
            elif thing['key'].startswith('/works/'):
                solr_doc = insert_work(thing)
                solr_docs += [solr_doc]
            elif thing['key'].startswith('/books/'):
                works = [w['key'] for w in thing.get('works', [])]
                if works:
                    for work_key in works:
                        solr_doc = solr.select(query='key:%s' % work_key).docs[0]
                        print(solr_doc)
                        update_work_w_edition(solr_doc, thing)
                        solr_docs += [solr_doc]
                else:
                    solr_doc = insert_edition_as_work(thing)
                    update_work_w_edition(solr_doc, thing)
                    solr_docs += [solr_doc]
            else:
                print("Unknown type: " + thing['key'])
                logger.error("Unknown type: " + thing['key'])
        except:
            print("Error for key: %s" % thing['key'])
            print(line)
            raise

        if solr_docs:
            chunk += [UpdateRequest(d) for d in solr_docs]
            if len(chunk) >= 250:
                solr_update(chunk)
                chunk = []

    if chunk:
        solr_update(chunk)
    solr.commit()
    solr.disconnect()