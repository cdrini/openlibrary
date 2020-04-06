"""Python library for accessing Solr.
"""
import json
import re

import web
import simplejson
import logging

from six.moves import urllib

logger = logging.getLogger("openlibrary.logger")
REQUEST_TIMEOUT = 3
CONTENT_TYPES = {
    'form': 'application/x-www-form-urlencoded',
    'json': 'application/json',
}

def urlencode(d, doseq=False):
    """There is a bug in urllib when used with unicode data.

        >>> d = {"q": u"\u0C05"}
        >>> urllib.parse.urlencode(d)
        'q=%E0%B0%85'
        >>> urllib.parse.urlencode(d, doseq=True)
        'q=%3F'

    This function encodes all the unicode strings in utf-8 before passing them to urllib.
    """
    def utf8(d):
        if isinstance(d, dict):
            return dict((utf8(k), utf8(v)) for k, v in d.items())
        elif isinstance(d, list):
            return [utf8(v) for v in d]
        else:
            return web.safestr(d)

    return urllib.parse.urlencode(utf8(d), doseq=doseq)

class Solr:
    def __init__(self, base_url):
        self.base_url = base_url
        self.host = urllib.parse.urlsplit(self.base_url)[1]

    def escape(self, query):
        r"""Escape special characters in the query string

            >>> solr = Solr("")
            >>> solr.escape("a[b]c")
            'a\\[b\\]c'
        """
        chars = r'+-!(){}[]^"~*?:\\'
        pattern = "([%s])" % re.escape(chars)
        return web.re_compile(pattern).sub(r'\\\1', query)

    def select(self, query, fields=None, facets=None,
               rows=None, start=None,
               doc_wrapper=None, facet_wrapper=None,
               **kw):
        """Execute a solr query.

        query can be a string or a dicitonary. If query is a dictionary, query
        is constructed by concatinating all the key-value pairs with AND condition.
        """
        params = {'wt': 'json'}

        for k, v in kw.items():
            # convert keys like facet_field to facet.field
            params[k.replace('_', '.')] = v

        params['q'] = self._prepare_select(query)

        if rows is not None:
            params['rows'] = rows
        params['start'] = start or 0

        if fields:
            params['fl'] = ",".join(fields)

        if facets:
            params['facet'] = "true"
            params['facet.field'] = []

            for f in facets:
                if isinstance(f, dict):
                    name = f.pop("name")
                    for k, v in f.items():
                        params["f.%s.facet.%s" % (name, k)] = v
                else:
                    name = f
                params['facet.field'].append(name)

        payload = urlencode(params, doseq=True)
        url = "/select"
        # switch to POST request when the payload is too big.
        # XXX: would it be a good idea to switch to POST always?
        if len(payload) < 500:
            url = url + "?" + payload
            resp = self._get(url)
        else:
            resp = self._post(url, payload)
        return self._parse_solr_result(
            simplejson.loads(resp),
            doc_wrapper=doc_wrapper,
            facet_wrapper=facet_wrapper)

    def _get(self, url, params=None):
        url = self.base_url + url
        if params:
            url += '?' + urlencode(params, doseq=True)
        logger.info("solr request: %s", url)
        return urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT).read()

    def _post(self, endpoint, payload, encoding='form'):
        """
        :param str endpoint:
        :param dict payload:
        :param 'form' | 'json' encoding:
        """
        if encoding == 'json':
            encoded_payload = simplejson.dumps(payload)
        elif encoding == 'form':
            encoded_payload = urlencode(payload, doseq=True)
        else:
            raise ValueError()

        url = self.base_url + endpoint
        logger.info("solr request: %s ...", url)
        request = urllib.request.Request(url, encoded_payload, {
            "Content-Type": "%s; charset=UTF-8" % CONTENT_TYPES[encoding]
        })
        return urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT).read()

    def get_doc(self, key):
        """
        Fetch a solr document directly. Usually do this if you want to update the doc
        :param str key: e.g. /works/OL1W
        :rtype: dict|None
        """
        result = self._get('/select', {'q': 'key:%s' % key, 'wt': 'json'})
        result = json.loads(result)
        docs = result['response'].get('docs', None)
        return docs and docs[0]

    def update_doc(self, doc):
        self._post('/update/json', {'add': {'doc': doc}}, encoding='json')

    def commit(self):
        self._get('/update', {'commit': 'true', 'wt': 'json'})

    def _parse_solr_result(self, result, doc_wrapper, facet_wrapper):
        response = result['response']

        doc_wrapper = doc_wrapper or web.storage
        facet_wrapper = facet_wrapper or (lambda name, value, count: web.storage(locals()))

        d = web.storage()
        d.num_found = response['numFound']
        d.docs = [doc_wrapper(doc) for doc in response['docs']]

        if 'facet_counts' in result:
            d.facets = {}
            for k, v in result['facet_counts']['facet_fields'].items():
                d.facets[k] = [facet_wrapper(k, value, count) for value, count in web.group(v, 2)]

        if 'highlighting' in result:
            d.highlighting = result['highlighting']

        if 'spellcheck' in result:
            d.spellcheck = result['spellcheck']

        return d

    def _prepare_select(self, query):
        def escape(v):
            # TODO: improve this
            return v.replace('"', r'\"').replace("(", "\\(").replace(")", "\\)")

        def escape_value(v):
            if isinstance(v, tuple): # hack for supporting range
                return "[%s TO %s]" % (escape(v[0]), escape(v[1]))
            elif isinstance(v, list): # one of
                return "(%s)" % " OR ".join(escape_value(x) for x in v)
            else:
                return '"%s"' % escape(v)

        if isinstance(query, dict):
            op = query.pop("_op", "AND")
            if op.upper() != "OR":
                op = "AND"
            op = " " + op + " "

            q = op.join('%s:%s' % (k, escape_value(v)) for k, v in query.items())
        else:
            q = query
        return q

solr = Solr('http://192.168.99.100:8983/solr')
doc = solr.get_doc('/works/OL3641574W')
solr.update_doc(doc)