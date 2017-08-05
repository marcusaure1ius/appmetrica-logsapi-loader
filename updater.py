#!/usr/bin/env python
"""
  logs-api.py

  This file is a part of the AppMetrica.

  Copyright 2017 YANDEX

  You may not use this file except in compliance with the License.
  You may obtain a copy of the License at:
        https://yandex.com/legal/metrica_termsofuse/
"""
import datetime
import logging

from db import Database
from fields import FieldsCollection
from logs_api import LogsApiClient
from state_storage import StateController

logger = logging.getLogger(__name__)


class Updater(object):
    def __init__(self,
                 logs_api_client: LogsApiClient,
                 db: Database,
                 fields_collection: FieldsCollection,
                 state_controller: StateController,
                 source_name: str,
                 table_name: str):
        self._logs_api_client = logs_api_client
        self._db = db
        self._table_name = table_name
        self._state_controller = state_controller
        self._source_name = source_name
        self._temp_table_load_name = '{}_tmp_data'.format(table_name)
        self._temp_table_insert_name = '{}_tmp_data_ins'.format(table_name)
        self._engine = 'MergeTree(EventDate, ' \
                       'cityHash64(DeviceID), ' \
                       '(EventDate, cityHash64(DeviceID)), ' \
                       '8192)'
        self._load_fields = fields_collection.get_load_fields()
        self._db_fields = fields_collection.get_db_fields()
        self._db_fields_str = ', '.join((f_name for (f_name, f_type)
                                         in self._db_fields))
        self._key_fields_str = ', '.join(fields_collection.get_db_keys())
        self._export_fields = fields_collection.get_export_keys_list()
        self._filed_converters = fields_collection.get_converters()

    def _create_tmp_table_for_insert(self, date1: datetime.date,
                                     date2: datetime.date):
        query = '''
            CREATE TABLE {db}.{tmp_ins_table} ENGINE = {engine}
            AS
            SELECT
                {fields_list}
            FROM {db}.{tmp_load_table}
            WHERE NOT (({key_fields_list}) 
                GLOBAL IN (SELECT
                    {key_fields_list}
                FROM {db}.{table}
                WHERE EventDate >= '{date1}' AND EventDate <= '{date2}'))
        '''.format(
            engine=self._engine,
            db=self._db.db_name,
            tmp_load_table=self._temp_table_load_name,
            tmp_ins_table=self._temp_table_insert_name,
            table=self._table_name,
            date1=date1.strftime('%Y-%m-%d'),
            date2=date2.strftime('%Y-%m-%d'),
            fields_list=self._db_fields_str,
            key_fields_list=self._key_fields_str
        )
        self._db.query(query)

    def _insert_data_to_prod(self):
        query = '''
            INSERT INTO {db}.{to_table}
                SELECT
                    {fields_list}
                FROM {db}.{from_table}
        '''.format(
            db=self._db.db_name,
            from_table=self._temp_table_insert_name,
            to_table=self._table_name,
            fields_list=self._db_fields_str
        )
        self._db.query(query)

    def prepare(self):
        if not self._db.database_exists():
            self._db.create_database()
            logger.info('Database "{}" created'.format(self._db.db_name))
        scheme = str((self._engine, tuple(self._db_fields)))
        table_exists = self._db.table_exists(self._table_name)
        scheme_valid = self._state_controller.is_valid_scheme(scheme)
        if not table_exists or not scheme_valid:
            self._db.drop_table(self._table_name)
            self._db.create_table(self._table_name, self._engine,
                                  self._db_fields)
            self._state_controller.update_db_scheme(scheme)
            logger.info('Table "{}" created'.format(self._table_name))

    def update(self, api_key: str, date: datetime.date):
        df = self._logs_api_client.load(api_key, self._source_name,
                                        self._load_fields,
                                        date, date)
        df = df.drop_duplicates()
        df['api_key'] = api_key
        for (name, converter) in self._filed_converters:
            df[name] = converter(df)

        self._db.drop_table(self._temp_table_load_name)
        self._db.drop_table(self._temp_table_insert_name)

        self._db.create_table(self._temp_table_load_name, self._engine,
                              self._db_fields)

        self._db.insert(
            self._temp_table_load_name,
            df[self._export_fields].to_csv(index=False, sep='\t')
        )
        self._create_tmp_table_for_insert(date, date)
        self._insert_data_to_prod()

        self._state_controller.mark_updated(api_key, date)

        self._db.drop_table(self._temp_table_load_name)
        self._db.drop_table(self._temp_table_insert_name)
