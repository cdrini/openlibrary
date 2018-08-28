"""Python library for accessing Solr.
"""
import httplib
import logging
import re
import urllib
import urllib2
import urlparse

import simplejson
import web

logger = logging.getLogger("openlibrary.logger")

def urlencode(d, doseq=False):
    """There is a bug in urllib when used with unicode data.

        >>> d = {"q": u"\u0C05"}
        >>> urllib.urlencode(d)
        'q=%E0%B0%85'
        >>> urllib.urlencode(d, doseq=True)
        'q=%3F'

    This function encodes all the unicode strings in utf-8 before passing them to urllib.
    """
    def utf8(d):
        if isinstance(d, dict):
            return dict((utf8(k), utf8(v)) for k, v in d.iteritems())
        elif isinstance(d, list):
            return [utf8(v) for v in d]
        else:
            return web.safestr(d)

    return urllib.urlencode(utf8(d), doseq=doseq)


class Solr:
    def __init__(self, base_url):
        self.base_url = base_url  # type: str
        self.host = urlparse.urlsplit(self.base_url)[1]  # type: str
        self.http_connection = None  # type: httplib.HTTPConnection

    def escape(self, query):
        r"""Escape special characters in the query string

            >>> solr = Solr("")
            >>> solr.escape("a[b]c")
            'a\\[b\\]c'
        """
        chars = r'+-!(){}[]^"~*?:\\'
        pattern = "([%s])" % re.escape(chars)
        return web.re_compile(pattern).sub(r'\\\1', query)

    def connect(self):
        """
        Create an http connection to be used for other POST calls. Useful for
        improving performance.
        """
        if self.http_connection:
            self.http_connection.close()

        self.http_connection = httplib.HTTPConnection(self.base_url)
        self.http_connection.connect()

    def disconnect(self):
        """
        Break any existing HTTP connections
        """
        if self.http_connection:
            self.http_connection.close()
            self.http_connection = None

    def update(self, requests, commit_within=60000):
        """
        Hit Solr's update endpoint to update an item
        :param list[UpdateRequest or DeleteRequest] requests:
        :param int commit_within:
        :return:
        """
        url = self.base_url + "/solr/update"
        if not url.startswith('http'):
            url = "http://" + url
        url += "?commitWithin=%d" % commit_within

        if self.http_connection:
            h1 = self.http_connection
        else:
            h1 = httplib.HTTPConnection(self.base_url)
            h1.connect()

        for r in requests:
            r = r.toxml()

            assert isinstance(r, basestring)
            h1.request('POST', url, r.encode('utf8'), {'Content-type': 'text/xml;charset=utf-8'})
            response = h1.getresponse()
            response_body = response.read()
            if response.reason != 'OK':
                logger.error(response.reason)
                logger.error(response_body)

        if not self.http_connection:
            h1.close()

    def commit(self):
        """
        Ask Solr to commit any pending changes
        """
        url = self.base_url + "/solr/update"
        if not url.startswith('http'):
            url = "http://" + url
        params_str = urlencode({'commit': 'true'})
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
        request = urllib2.Request(url, params_str, headers)
        return urllib2.urlopen(request, timeout=3).read()

    def select(self, query, fields=None, facets=None,
               rows=None, start=None,
               doc_wrapper=None, facet_wrapper=None,
               **kw):
        """
        Execute a Solr query.

        :param str or dict query: query is a dictionary, query is constructed
            by concatenating all the key-value pairs with AND condition
        :param list[str] fields: solr fields to retrieve
        :param list[str] or dict facets:
        :param int rows: number of rows to return
        :param int start: offset
        :param fn doc_wrapper:
        :param fn facet_wrapper:
        :param dict kw: other param arguments; use '_' inplace of '.' in the keys
        :return: a response object
        :rtype: web.storage
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

        # switch to POST request when the payload is too big.
        # XXX: would it be a good idea to swithc to POST always?
        payload = urlencode(params, doseq=True)
        url = self.base_url + "/select"
        if len(payload) < 500:
            url = url + "?" + payload
            logger.info("solr request: %s", url)
            data = urllib2.urlopen(url, timeout=3).read()
        else:
            logger.info("solr request: %s ...", url)
            request = urllib2.Request(url, payload, {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
            data = urllib2.urlopen(request, timeout=3).read()
        return self._parse_solr_result(
            simplejson.loads(data),
            doc_wrapper=doc_wrapper,
            facet_wrapper=facet_wrapper)

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

if __name__ == '__main__':
    import doctest
    doctest.testmod()
