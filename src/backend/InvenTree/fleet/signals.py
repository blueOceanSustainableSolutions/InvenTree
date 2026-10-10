"""Signal receivers for the fleet app.

trigger_event only reaches plugins, so the fleet app listens to Django
signals instead.
"""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

import structlog

import InvenTree.ready
from build.models import Build

logger = structlog.get_logger('inventree')


@receiver(post_save, sender=Build, dispatch_uid='fleet_build_post_save')
def fleet_build_saved(sender, instance: Build, **kwargs):
    """Create or update the deployment of a fleet build order.

    A fleet error must never block a build order: the fleet writes run in a
    savepoint, so a failure only rolls back those writes and is logged. The
    build save (and an enclosing build action) carries on, and the
    fleet_sync_pipeline task catches the deployment up later.
    """
    if InvenTree.ready.isImportingData() or not InvenTree.ready.canAppAccessDatabase(
        allow_test=True
    ):
        return

    from fleet.services.pipeline import is_fleet_build, on_build_saved

    # One query, so that other build orders pay (almost) nothing
    if not is_fleet_build(instance):
        return

    try:
        with transaction.atomic():
            on_build_saved(instance)
    except Exception:
        logger.exception(
            'Fleet: failed to update the deployment of build order %s',
            instance.reference,
        )
