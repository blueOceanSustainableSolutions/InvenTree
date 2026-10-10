"""Deterministic fake data platform, for development and tests.

Tests control the output through class attributes:

    MockDataPlatformClient.statuses = {'AUR-0161': DeviceStatus(...)}
    MockDataPlatformClient.error = 'timeout'  # every call fails
    MockDataPlatformClient.generate = False   # unknown devices do not report
    MockDataPlatformClient.reset()            # back to the defaults

By default (generate=True), a device without an explicit status reports every
enabled stream of its active deployment as seen now, at its nominal position.
"""

from django.utils import timezone

from fleet.integrations.base import (
    DataPlatformClient,
    DataPlatformError,
    DeviceStatus,
    Position,
    StreamStatus,
)


class MockDataPlatformClient(DataPlatformClient):
    """Data platform client which returns canned or generated statuses."""

    statuses: dict[str, DeviceStatus] = {}
    error: str | None = None
    generate: bool = True
    calls: list[list[str]] = []

    @classmethod
    def reset(cls):
        """Restore the default behaviour."""
        cls.statuses = {}
        cls.error = None
        cls.generate = True
        cls.calls = []

    def get_status(self, platform_ids: list[str]) -> dict[str, DeviceStatus]:
        """Return the canned status of each device, or a generated one."""
        type(self).calls.append(list(platform_ids))

        if self.error:
            raise DataPlatformError(self.error)

        result = {}

        for platform_id in platform_ids:
            if platform_id in self.statuses:
                result[platform_id] = self.statuses[platform_id]
            elif self.generate and (status := self.generate_status(platform_id)):
                result[platform_id] = status

        return result

    def generate_status(self, platform_id: str) -> DeviceStatus | None:
        """Report every enabled stream as seen now, at the nominal position."""
        from fleet.models import Deployment
        from fleet.status_codes import DeploymentStatus

        deployment = (
            Deployment.objects
            .filter(
                device__fleet_link__platform_id=platform_id,
                status=DeploymentStatus.DEPLOYED.value,
            )
            .select_related('site')
            .first()
        )

        if deployment is None:
            return None

        now = timezone.now()

        streams = [
            StreamStatus(key=key, last_seen=now)
            for key in deployment.streams.filter(enabled=True).values_list(
                'key', flat=True
            )
        ]

        position = None
        latitude = deployment.latitude
        longitude = deployment.longitude

        if latitude is None and deployment.site:
            latitude = deployment.site.latitude
            longitude = deployment.site.longitude

        if latitude is not None and longitude is not None:
            position = Position(
                latitude=float(latitude),
                longitude=float(longitude),
                timestamp=now,
                source='mock',
            )

        return DeviceStatus(
            platform_id=platform_id,
            streams=streams,
            position=position,
            raw={'mock': True},
        )

    def list_devices(self) -> list[dict]:
        """Return the canned devices."""
        return [{'platform_id': platform_id} for platform_id in self.statuses]
