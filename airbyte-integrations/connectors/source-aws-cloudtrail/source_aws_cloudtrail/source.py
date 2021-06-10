# MIT License
#
# Copyright (c) 2020 Airbyte
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


from abc import ABC
from typing import Any, Iterable, List, Mapping, MutableMapping, Optional, Tuple
from datetime import datetime


import boto3
import botocore
from botocore.config import Config

from airbyte_cdk import AirbyteLogger
from airbyte_cdk.sources import AbstractSource
from airbyte_cdk.sources.streams import Stream
from airbyte_cdk.models import SyncMode


class AwsCloudtrailStream(Stream, ABC):

    limit: int = 25

    start_date_format = "%Y-%m-%d"

    def __init__(self, aws_key_id: str, aws_secret_key: str, start_date: str, **kwargs):
        self.aws_secret_key = aws_secret_key
        self.aws_key_id = aws_key_id
        self.start_date = self.datetime_to_timestamp(datetime.strptime(start_date, self.start_date_format))

        config = Config(
            parameter_validation=False,
            retries = dict(
                # use similar configuration as in http source
                max_attempts=5,
                # https://boto3.amazonaws.com/v1/documentation/api/latest/guide/retries.html#adaptive-retry-mode
                mode='adaptive',
            )
        )

        self.client = boto3.client(
            'cloudtrail',
            aws_access_key_id=aws_key_id,
            aws_secret_access_key=aws_secret_key,
            config=config
        )

    def next_page_token(self, response: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        return response.get('NextToken')

    def request_params(
        self, stream_state: Mapping[str, Any], stream_slice: Mapping[str, any] = None, next_page_token: Mapping[str, Any] = None
    ) -> MutableMapping[str, Any]:
        params = {"MaxResults": self.limit}

        if self.start_date:
            params["StartTime"] = self.start_date
        if next_page_token:
            params['NextToken'] = next_page_token
        return params


    def datetime_to_timestamp(self, date: datetime) -> int:
        return int(datetime.timestamp(date))


class IncrementalAwsCloudtrailStream(AwsCloudtrailStream, ABC):

    cursor_field = "StartTime"

    @property
    def limit(self):
        return super().limit

    state_checkpoint_interval = limit

    def get_updated_state(
        self,
        current_stream_state:
        MutableMapping[str, Any],
        latest_record: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        record_time = latest_record[self.time_field]
        return {self.cursor_field: max(record_time, current_stream_state.get(self.cursor_field, 0))}

    def request_params(self, stream_state=None, **kwargs):
        params = super().request_params(stream_state=stream_state, **kwargs)
        cursor_data = stream_state.get(self.cursor_field)
        if cursor_data:
            params[self.cursor_field] = cursor_data

        return params

    def read_records(
        self,
        sync_mode: SyncMode,
        cursor_field: List[str] = None,
        stream_slice: Mapping[str, Any] = None,
        stream_state: Mapping[str, Any] = None,
    ) -> Iterable[Mapping[str, Any]]:
        stream_state = stream_state or {}
        pagination_complete = False

        next_page_token = None
        while not pagination_complete:
            params=self.request_params(stream_state=stream_state, stream_slice=stream_slice, next_page_token=next_page_token)
            response = self.send_request(**params)

            yield from self.parse_response(response)

            next_page_token = self.next_page_token(response)
            if not next_page_token:
                pagination_complete = True

        yield from []


class Events(IncrementalAwsCloudtrailStream):

    cursor_field = "StartTime"

    primary_key = "EventId"

    time_field = "EventTime"

    data_field = "Events"

    def send_request(self, **kwargs):
        return self.client.lookup_events(**kwargs)

    def parse_response(self, response: dict, **kwargs) -> Iterable[Mapping]:
        for event in response[self.data_field]:
            event["EventTime"] = self.datetime_to_timestamp(event["EventTime"])
            yield event


class SourceAwsCloudtrail(AbstractSource):
    def check_connection(self, logger: AirbyteLogger, config: Mapping[str, Any]) -> Tuple[bool, any]:
        client = boto3.client('cloudtrail', aws_access_key_id=config['aws_key_id'], aws_secret_access_key=config['aws_secret_key'])
        try:
            client.lookup_events(MaxResults=1)
        except botocore.exceptions.ClientError as error:
            return False, error

        return True, None

    def streams(self, config: Mapping[str, Any]) -> List[Stream]:
        return [Events(**config)]
