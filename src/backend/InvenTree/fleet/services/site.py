"""Changing the location of a site.

The site is the only place which says where a device should be. Its location
is entered when the site is created (or while it is still empty); after that
it changes only through move_site(), which records the old location, the new
one and the reason in a SiteLocationChange.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fleet.models import Site, SiteLocationChange

# Fields which only move_site() changes once the site has a location
LOCATION_FIELDS = ['latitude', 'longitude', 'depth_m']


@transaction.atomic
def move_site(
    site: Site, user, latitude, longitude, comment: str, depth_m=None, effective_at=None
) -> SiteLocationChange:
    """Move a site to a new location, and record the change.

    Args:
        site: The site to move
        user: The user recording the change
        latitude: New latitude
        longitude: New longitude
        comment: Why the site was moved (required)
        depth_m: New depth; None keeps the current depth
        effective_at: When the device was actually moved (defaults to now).
            It cannot be in the future, nor before the last recorded change.
    """
    Site.objects.select_for_update().filter(pk=site.pk).first()
    site.refresh_from_db()

    if latitude is None or longitude is None:
        raise ValidationError(_('Latitude and longitude are both required'))

    comment = (comment or '').strip()

    if not comment:
        raise ValidationError({'comment': _('Say why the site is moved')})

    now = timezone.now()
    effective_at = effective_at or now

    if effective_at > now:
        raise ValidationError({'effective_at': _('Cannot be in the future')})

    last = site.location_changes.order_by('-effective_at', '-pk').first()

    if last is not None and effective_at < last.effective_at:
        raise ValidationError(
            {
                'effective_at': _('Cannot be before the last move of the site')
                + f' ({last.effective_at:%Y-%m-%d %H:%M})'
            }
        )

    if depth_m is None:
        depth_m = site.depth_m

    if (
        site.latitude == latitude
        and site.longitude == longitude
        and site.depth_m == depth_m
    ):
        raise ValidationError(_('The site is already at this location'))

    change = SiteLocationChange(
        site=site,
        effective_at=effective_at,
        old_latitude=site.latitude,
        old_longitude=site.longitude,
        old_depth_m=site.depth_m,
        latitude=latitude,
        longitude=longitude,
        depth_m=depth_m,
        comment=comment[:500],
        created_by=user if user is not None and user.is_authenticated else None,
    )
    change.full_clean(exclude=['site', 'created_by'])
    change.save()

    site.latitude = latitude
    site.longitude = longitude
    site.depth_m = depth_m
    site.save()

    return change
