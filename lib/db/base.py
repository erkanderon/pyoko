# -*-  coding: utf-8 -*-
"""
this module contains a base class for other db access classes
"""

# Copyright (C) 2015 ZetaOps Inc.
#
# This file is licensed under the GNU General Public License v3
# (GPLv3).  See LICENSE.txt for details.
from connection import *
from lib.py2map import Dictomap
from lib.utils import DotDict


class MultipleObjectsReturned(Exception):
    """The query returned multiple objects when only one was expected."""
    pass


# TODO: Implement basic functinality of "new" method
# TODO: Add schema support for new method
# TODO: Add OR support
# TODO: Implement schema migration for Riak JSON data

class SolRiakcess(object):
    """
    This class implements Django-esque query APIs with aim of fusing the Solr and Riak in a more pythonic way
    """

    def __init__(self, **config):
        self.bucket = riak.RiakBucket
        self._cfg = DotDict(config)
        self._cfg.client = self._cfg.client or pbc_client
        self.datatype = None  # we convert new object data according to bucket datatype, eg: Dictomaping for 'map' type
        self.return_solr_result = False  # don't go to riak, solr results are enough
        self.new_value = None  # value of the to be created by .new(**params).save(key)

        self.solr_result_set = {}  # caching solr results, for repeating calls
        self.solr_query = set()  # query parts, will be compiled before execution
        self.solr_params = {}  # solr search parameters. eg: rows, fl, start, sort etc.
        # All the following properties used for caching the previous query.
        self.solr_preceding_params = {}  # preceding solr search parameters. eg: rows, fl, start, sort etc.
        self.solr_preceding_query = ''  # previously executed solr query
        self.riak_cache = []  # caching riak result, for repeating iterations on same query
        self.re_fetch_from_riak = True  # if we get fresh results from solr

    # ######## Development Methods  #########

    def watch(self):
        print "_cfg: ", self._cfg
        print "solr_result_set : ", self.solr_result_set
        print "solr_query : ", self.solr_query
        print "solr_params : ", self.solr_params
        print "last_query : ", self.solr_preceding_query
        print "riak_cache : ", self.riak_cache
        print "fresh_results : ", self.re_fetch_from_riak
        print "return_solr : ", self.return_solr_result
        print "new_value : ", self.new_value
        print "solr_preceding_params : ", self.solr_preceding_params
        print "solr_preceding_query : ", self.solr_preceding_query
        return self


    # ######## Riak Methods  #########

    def set_bucket(self, type, name):
        self._cfg.bucket_type = type
        self._cfg.bucket_name = name
        self.bucket = self._cfg.client.bucket_type(self._cfg.bucket_type).bucket(self._cfg.bucket_name)
        if 'index' not in self._cfg:
            self._cfg.index = self._cfg.bucket_name
        self.datatype = self.bucket.get_properties().get('datatype', None)
        return self

    def count_bucket(self):
        return sum([len(key_list) for key_list in self.bucket.stream_keys()])

    def new(self, **kwargs):
        """
        this will populate a new object using kwargs on top of latest version of the object schema
        :param kwargs:
        :return:
        """
        raise NotImplemented

    def save(self, key, value=None):
        value = value or self.new_value
        if self.datatype == 'map' and isinstance(value, dict):
            return Dictomap(self.bucket, value, str(key)).map.store()
        else:
            return self.bucket.new(key, value).store()

    def _get_from_db(self):
        if self.re_fetch_from_riak:
            self.riak_cache = self.bucket.multiget(map(lambda k: k['_yz_rk'], self.solr_result_set['docs']))
        self.reset_query()
        return self.riak_cache

    def _delete_all(self):
        """
        for development purposes, normally we should never delete anything, let alone whole bucket!
        """
        count = self.count_bucket()
        for pck in self.bucket.stream_keys():
            for k in pck:
                self.bucket.get(k).delete()
        return count

    def _get(self):
        self._exec_query()
        if self.re_fetch_from_riak:
            self.riak_cache = [self.bucket.get(self.solr_result_set['docs'][0]['_yz_rk'])]
        return self.riak_cache[0]

    # ######## Solr/Query Related Methods  #########

    def filter(self, **filters):
        """
        this will support OR and other more advanced queries as well
        """
        for key, val in filters.items():
            key = key.replace('__', '.')
            if val is None:
                key = '-%s' % key
                val = '[* TO *]'
            self.solr_query.add("%s:%s" % (key, val))
        return self

    def all(self):
        self.set_solr_conf(fl='_yz_rk')
        return self

    def get(self):
        self._exec_query()
        if self.count() > 1:
            raise MultipleObjectsReturned()
        return self._get()


    def count(self):
        self._exec_query(rows=0)
        return self.solr_result_set['num_found']

    def reset_query(self):
        # self.solr_result_set.clear()
        self.solr_params = {}
        self.return_solr_result = False
        self.solr_query.clear()

    def _query(self, query):
        self.solr_query.add(query)
        return self

    def set_solr_conf(self, **params):
        """
        add/update solr query parameters
        """
        self.solr_params.update(params)
        return self

    def _compile_query(self):
        """
        this will support "OR" and maybe other more advanced queries as well
        :param final: Bool, Should be True if we will execute the returned query immediately
        :return: Solr query string
        """
        if not self.solr_query:
            self.solr_query.add('*:*')  # get everything
        anded = ' AND '.join(self.solr_query)
        query = anded
        return query

    def re_search_required(self, compiled_query_string):
        return compiled_query_string != self.solr_preceding_query or self.solr_params != self.solr_preceding_params

    def _exec_query(self, **params):
        self.solr_params.update(params)
        compiled_query_string = self._compile_query()
        if self.re_search_required(compiled_query_string):
            self.solr_result_set = self.bucket.search(compiled_query_string, self._cfg.index, **self.solr_params)
            self.re_fetch_from_riak = True
            self.solr_preceding_params = self.solr_params
            self.solr_preceding_query = compiled_query_string
        else:
            self.re_fetch_from_riak = False
        return self

    def solr(self):
        """
        returns raw solr result set
        """
        self.return_solr_result = True
        return self

    def _get_from_solr(self):
        results = self.solr_result_set['docs']
        self.reset_query()
        return results

    # ######## Python Magic  #########

    def __iter__(self):
        self._exec_query()
        return iter(self._get_from_db() if not self.return_solr_result else self.solr_result_set['docs'])

    def __getitem__(self, index):
        if isinstance(index, int):
            self.set_solr_conf(rows=1, start=index)
            return self._get()
        elif isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            self.set_solr_conf(rows=stop - start, start=start)
            return self
        else:
            raise TypeError("index must be int or slice")


