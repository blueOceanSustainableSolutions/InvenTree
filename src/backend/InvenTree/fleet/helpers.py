"""Helper functions for the fleet app."""

from datetime import timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from common.settings import get_global_setting


def get_model_setting(key: str):
    """Return the model instance selected in a model-type global setting.

    The model class comes from the setting definition ('model'), through
    InvenTreeSetting.model_class(). The cache is skipped, because the worker
    must see a change made through the server straight away.

    Arguments:
        key: The setting key (e.g. 'FLEET_WORKSHOP_LOCATION')

    Returns:
        The selected instance, or None if the setting is empty or invalid
    """
    from common.models import InvenTreeSetting

    value = get_global_setting(key, cache=False)

    if not value:
        return None

    model = InvenTreeSetting.get_setting_object(key, cache=False).model_class()

    if model is None:
        return None

    try:
        return model.objects.get(pk=int(value))
    except (ValueError, TypeError, model.DoesNotExist):
        return None


def to_local_date(value):
    """Return the local date of a datetime (aware or naive)."""
    if value is None:
        return None

    if timezone.is_aware(value):
        return timezone.localtime(value).date()

    return value.date()


def normalize_datetime(value):
    """Return a datetime which can be compared with timezone.now().

    Data platform timestamps are usually aware, but timezone.now() is naive
    when USE_TZ is off (e.g. in the backend tests).
    """
    if value is None:
        return None

    if settings.USE_TZ and timezone.is_naive(value):
        return timezone.make_aware(value, dt_timezone.utc)

    if not settings.USE_TZ and timezone.is_aware(value):
        return timezone.make_naive(value)

    return value


# Clock difference allowed between the device which enters a date and the server
FIELD_DATE_TOLERANCE = timedelta(minutes=5)


def check_field_date(value, field: str, earliest=None, now=None):
    """Check a date of field work entered by hand (it can be in the past).

    Field work is often entered later, when the technician is back online:
    the date is when the work was done, not when it was entered.

    Arguments:
        value: The date entered
        field: The form field which reports an error
        earliest: The date cannot be before this one (e.g. the deployment)
        now: The current time (defaults to timezone.now())

    Returns:
        The date, comparable with timezone.now()

    Raises:
        ValidationError: The date is in the future, or before the earliest one
    """
    from django.core.exceptions import ValidationError
    from django.utils.formats import date_format
    from django.utils.translation import gettext_lazy as _

    value = normalize_datetime(value)
    now = now or timezone.now()

    if value > now + FIELD_DATE_TOLERANCE:
        raise ValidationError({field: _('The date cannot be in the future')})

    earliest = normalize_datetime(earliest)

    if earliest is not None and value < earliest:
        raise ValidationError({
            field: _('The date cannot be before')
            + ' '
            + date_format(earliest, 'SHORT_DATETIME_FORMAT')
        })

    return min(value, now)
