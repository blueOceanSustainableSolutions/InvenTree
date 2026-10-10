"""HTTP client for the cloud data dashboard API.

The API specification is still an open question (FLEET_PLAN.md section 12,
question 1). Until it is known, this client assumes the following contract,
and only parse_device() and the request in get_status() should need to change:

    GET {FLEET_DATA_API_URL}/devices/status/?ids=<id>,<id>
    Authorization: Bearer {FLEET_DATA_API_TOKEN}

    {"devices": [
        {"id": "AUR-0161",
         "streams": [{"key": "hydrophone", "last_seen": "2026-10-08T10:00:00Z"}],
         "position": {"latitude": 37.01, "longitude": -7.93,
                      "timestamp": "2026-10-08T10:00:00Z"}}
    ]}
"""

from django.utils.dateparse import parse_datetime

import requests
import structlog

from common.settings import get_global_setting
from fleet.integrations.base import (
    DataPlatformClient,
    DataPlatformError,
    DeviceStatus,
    Position,
    StreamStatus,
)

logger = structlog.get_logger('inventree')

# Request timeout (seconds)
TIMEOUT = 10


def parse_timestamp(value):
    """Parse an ISO timestamp, returning None if it is missing or invalid."""
    if not value:
        return None

    try:
        return parse_datetime(str(value))
    except ValueError:
        return None


def parse_device(item: dict) -> DeviceStatus | None:
    """Convert one device of the API response into a DeviceStatus."""
    platform_id = item.get('id') or item.get('platform_id')

    if not platform_id:
        return None

    streams = [
        StreamStatus(key=str(s['key']), last_seen=parse_timestamp(s.get('last_seen')))
        for s in item.get('streams') or []
        if isinstance(s, dict) and s.get('key')
    ]

    position = None
    pos = item.get('position')

    if isinstance(pos, dict):
        try:
            timestamp = parse_timestamp(pos.get('timestamp'))

            if timestamp is not None:
                position = Position(
                    latitude=float(pos['latitude']),
                    longitude=float(pos['longitude']),
                    timestamp=timestamp,
                    source=str(pos.get('source') or 'platform'),
                )
        except (KeyError, TypeError, ValueError):
            logger.warning('Fleet data platform: invalid position for %s', platform_id)

    return DeviceStatus(
        platform_id=str(platform_id), streams=streams, position=position, raw=item
    )


class HttpDataPlatformClient(DataPlatformClient):
    """Client for the data dashboard API (bearer token authentication)."""

    def __init__(self, base_url: str | None = None, token: str | None = None):
        """Read the URL and token from the global settings unless given."""
        self.base_url = (
            base_url
            if base_url is not None
            else get_global_setting('FLEET_DATA_API_URL', cache=False)
        ) or ''
        self.token = (
            token
            if token is not None
            else get_global_setting('FLEET_DATA_API_TOKEN', cache=False)
        ) or ''

    def request(self, path: str, params: dict | None = None):
        """Send a GET request to the API and return the decoded JSON."""
        if not self.base_url:
            raise DataPlatformError('FLEET_DATA_API_URL is not set')

        url = self.base_url.rstrip('/') + '/' + path.lstrip('/')
        headers = {'Accept': 'application/json'}

        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DataPlatformError(str(exc)) from exc

    def get_status(self, platform_ids: list[str]) -> dict[str, DeviceStatus]:
        """Return the live status of the given devices."""
        if not platform_ids:
            return {}

        data = self.request('devices/status/', params={'ids': ','.join(platform_ids)})

        if not isinstance(data, dict) or not isinstance(data.get('devices'), list):
            raise DataPlatformError('Unexpected response from the data platform')

        result = {}

        for item in data['devices']:
            if isinstance(item, dict) and (status := parse_device(item)):
                result[status.platform_id] = status

        return result

    def list_devices(self) -> list[dict]:
        """Return the devices known to the platform."""
        data = self.request('devices/')

        if isinstance(data, dict):
            data = data.get('devices', [])

        return data if isinstance(data, list) else []
