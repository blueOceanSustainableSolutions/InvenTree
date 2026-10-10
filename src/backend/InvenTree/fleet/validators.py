"""Validation methods for the fleet app."""

import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.utils.regex_helper import _lazy_re_compile
from django.utils.translation import gettext_lazy as _


def generate_next_site_reference():
    """Generate the next available Site reference."""
    from fleet.models import Site

    return Site.generate_reference()


def validate_site_reference_pattern(pattern):
    """Validate the Site reference 'pattern' setting."""
    from fleet.models import Site

    Site.validate_reference_pattern(pattern)


def validate_site_reference(value):
    """Validate that the Site reference field matches the required pattern."""
    from fleet.models import Site

    Site.validate_reference_field(value)


def generate_next_deployment_reference():
    """Generate the next available Deployment reference."""
    from fleet.models import Deployment

    return Deployment.generate_reference()


def validate_deployment_reference_pattern(pattern):
    """Validate the Deployment reference 'pattern' setting."""
    from fleet.models import Deployment

    Deployment.validate_reference_pattern(pattern)


def validate_deployment_reference(value):
    """Validate that the Deployment reference field matches the required pattern."""
    from fleet.models import Deployment

    Deployment.validate_reference_field(value)


def generate_next_task_reference():
    """Generate the next available MaintenanceTask reference."""
    from fleet.models import MaintenanceTask

    return MaintenanceTask.generate_reference()


def validate_task_reference_pattern(pattern):
    """Validate the MaintenanceTask reference 'pattern' setting."""
    from fleet.models import MaintenanceTask

    MaintenanceTask.validate_reference_pattern(pattern)


def validate_task_reference(value):
    """Validate that the MaintenanceTask reference field matches the required pattern."""
    from fleet.models import MaintenanceTask

    MaintenanceTask.validate_reference_field(value)


def generate_next_trip_reference():
    """Generate the next available FieldTrip reference."""
    from fleet.models import FieldTrip

    return FieldTrip.generate_reference()


def validate_trip_reference_pattern(pattern):
    """Validate the FieldTrip reference 'pattern' setting."""
    from fleet.models import FieldTrip

    FieldTrip.validate_reference_pattern(pattern)


def validate_trip_reference(value):
    """Validate that the FieldTrip reference field matches the required pattern."""
    from fleet.models import FieldTrip

    FieldTrip.validate_reference_field(value)


def generate_next_alert_reference():
    """Generate the next available Alert reference."""
    from fleet.models import Alert

    return Alert.generate_reference()


def validate_alert_reference_pattern(pattern):
    """Validate the Alert reference 'pattern' setting."""
    from fleet.models import Alert

    Alert.validate_reference_pattern(pattern)


def validate_alert_reference(value):
    """Validate that the Alert reference field matches the required pattern."""
    from fleet.models import Alert

    Alert.validate_reference_field(value)


def validate_email_list(value):
    """Validate a comma-separated list of email addresses (may be empty)."""
    for address in str(value or '').split(','):
        if address := address.strip():
            validate_email(address)


class OptionalURLValidator(URLValidator):
    """http(s) URL validator which also accepts host names without a TLD.

    e.g. https://bo-server:8444/fleet/ on a LAN.
    """

    host_re = (
        '('
        + URLValidator.hostname_re
        + URLValidator.domain_re
        + '('
        + URLValidator.tld_re
        + ')?'
        + '|localhost)'
    )

    regex = _lazy_re_compile(
        r'^(?:[a-z0-9.+-]*)://'
        r'(?:[^\s:@/]+(?::[^\s:@/]*)?@)?'
        r'(?:' + URLValidator.ipv4_re + '|' + URLValidator.ipv6_re + '|' + host_re + ')'
        r'(?::[0-9]{1,5})?'
        r'(?:[/?#][^\s]*)?'
        r'\Z',
        re.IGNORECASE,
    )


def validate_optional_url(value):
    """Validate an http(s) URL setting (may be empty).

    The core BaseURLValidator is not used: it is locked to the site URL.
    """
    if value := str(value or '').strip():
        OptionalURLValidator(schemes=['http', 'https'])(value)


def validate_geofence_polygon(value):
    """Validate a geofence polygon, given as a list of [latitude, longitude] points.

    The ring may be open or closed, and must contain at least three distinct points.
    """
    if value in (None, ''):
        return

    if not isinstance(value, list):
        raise ValidationError(
            _('Polygon must be a list of [latitude, longitude] points')
        )

    points = []

    for point in value:
        if (
            not isinstance(point, (list, tuple))
            or len(point) != 2
            or not all(
                isinstance(c, (int, float)) and not isinstance(c, bool) for c in point
            )
        ):
            raise ValidationError(
                _('Each polygon point must be a [latitude, longitude] pair')
            )

        lat, lon = point

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValidationError(_('Polygon point is out of range'))

        points.append((lat, lon))

    if len(set(points)) < 3:
        raise ValidationError(_('Polygon must contain at least three distinct points'))
