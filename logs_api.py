#!/usr/bin/env python
"""
  logs_api.py

  This file is a part of the AppMetrica.

  Copyright 2017 YANDEX

  You may not use this file except in compliance with the License.
  You may obtain a copy of the License at:
        https://yandex.com/legal/metrica_termsofuse/
"""
import datetime
import json
import logging
import time
from typing import List, Generator

import pandas as pd
import requests
from pandas import DataFrame

logger = logging.getLogger(__name__)


class LogsApiClient(object):
    def __init__(self, token: str, chunk_size: int):
        self.token = token
        self._chunk_size = chunk_size

    def app_creation_date(self, api_key: str) -> str:
        url = 'https://api.appmetrica.yandex.ru/management/v1/application' \
              '/{api_key}'.format(api_key=api_key)
        params = {
            'oauth_token': self.token
        }

        r = requests.get(url, params=params)
        create_date = None
        if r.status_code == 200:
            app_details = json.load(r.text)
            if ('application' in app_details) \
                    and ('create_date' in app_details['application']):
                create_date = app_details['application']['create_date']
        return create_date

    def _request_logs_api(self,
                          api_key: str,
                          table: str,
                          fields: List[str],
                          date_from: datetime.date,
                          date_to: datetime.date,
                          parts_count: int,
                          part_number: int):
        url = 'https://api.appmetrica.yandex.ru/logs/v1/export/{table}.csv' \
            .format(table=table)
        time_from = datetime.datetime.combine(date_from, datetime.time.min)
        time_to = datetime.datetime.combine(date_to, datetime.time.max)
        format = '%Y-%m-%d %H:%M:%S'
        params = {
            'application_id': api_key,
            'date_since': time_from.strftime(format),
            'date_until': time_to.strftime(format),
            'date_dimension': 'default',
            'fields': ','.join(fields),
            'oauth_token': self.token
        }
        if parts_count > 1:
            params.update({
                'parts_count': parts_count,
                'part_number': part_number,
            })
        return requests.get(url, params=params, stream=True)

    def _response_chunks(self, response: requests.Response):
        compression = response.headers.get('Content-Encoding')
        return pd.read_csv(response.raw,
                           compression=compression,
                           encoding=response.encoding,
                           chunksize=self._chunk_size,
                           iterator=True)

    def load(self, api_key: str, table: str, fields: List[str],
             date_from: datetime.date, date_to: datetime.date) \
            -> Generator[DataFrame, None, None]:
        parts_count = 1
        part_number = 0
        while part_number < parts_count:
            r = self._request_logs_api(api_key=api_key, table=table,
                                       fields=fields, date_from=date_from,
                                       date_to=date_to,
                                       parts_count=parts_count,
                                       part_number=part_number)
            logger.debug('Logs API response code: {}'.format(r.status_code))
            if r.status_code == 200:
                for df in self._response_chunks(r):
                    yield df
                part_number += 1
            else:
                logger.debug(r.text)
                if r.status_code == 202:
                    time.sleep(10)
                elif r.status_code == 429:
                    time.sleep(60)
                elif r.status_code == 400 \
                        and 'Try to use more parts.' in r.text:
                    parts_count *= 2
                    part_number = 0
                else:
                    raise ValueError('[{}] {}'.format(r.status_code, r.text))