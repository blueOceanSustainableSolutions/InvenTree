"""Interface of the data platform client.

The monitoring service only talks to a DataPlatformClient, so the real HTTP
client and the mock client can be swapped with the FLEET_DATA_PROVIDER setting.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from common.settings import get_global_setting


class DataPlatformError(Exception):
    """The data platform could not be reached, or returned an invalid response."""


@dataclass
class StreamStatus:
    """Latest data received for one stream of a device."""

    key: str
    last_seen: datetime | None


@dataclass
class Position:
    """Latest reported position of a device."""

    latitude: float
    longitude: float
    timestamp: datetime
    source: str = 'platform'


@dataclass
class DeviceStatus:
    """Live status of one device, as reported by the data platform."""

    platform_id: str
    streams: list[StreamStatus] = field(default_factory=list)
    position: Position | None = None
    raw: dict = field(default_factory=dict)


class DataPlatformClient(ABC):
    """Interface to the data platform."""

    # Maximum number of devices requested in one call
    BATCH_SIZE = 50

    @abstractmethod
    def get_status(self, platform_ids: list[str]) -> dict[str, DeviceStatus]:
        """Return the live status of the given devices.

        Devices unknown to the platform are left out of the result.

        Raises:
            DataPlatformError: if the platform cannot be reached
        """

    def list_devices(self) -> list[dict]:
        """Return the devices known to the platform (optional, for linking)."""
        return []


def get_client() -> DataPlatformClient:
    """Return the data platform client selected by FLEET_DATA_PROVIDER."""
    provider = get_global_setting('FLEET_DATA_PROVIDER', cache=False)

    if provider == 'http':
        from fleet.integrations.http_client import HttpDataPlatformClient

        return HttpDataPlatformClient()

    from fleet.integrations.mock_client import MockDataPlatformClient

    return MockDataPlatformClient()
