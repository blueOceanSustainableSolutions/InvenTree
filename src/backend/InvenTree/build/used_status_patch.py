"""Patches Build methods to propagate USED status.

If any stock item consumed by a build (or build output) has the USED
custom status (key=15), the output stock item is also marked USED.
"""

import logging

from common.models import InvenTreeCustomUserStateModel

logger = logging.getLogger('inventree')

USED_CUSTOM_KEY = 15


def _patch_complete_build_output():
    """Wrap Build.complete_build_output to mark output USED if any consumed item was USED.

    Catches the case where a single output is completed (items_to_install is populated).
    """
    from build.models import Build

    original = Build.complete_build_output

    def wrapper(self, output, user, quantity=None, **kwargs):
        allocated_items = list(output.items_to_install.all())
        original(self, output, user, quantity, **kwargs)
        has_used = any(
            item.stock_item.status_custom_key == USED_CUSTOM_KEY
            for item in allocated_items
        )
        if has_used:
            try:
                used_state = InvenTreeCustomUserStateModel.objects.get(
                    reference_status='StockStatus', key=USED_CUSTOM_KEY
                )
                output.status_custom_key = used_state.key
                output.status = used_state.logical_key
                output.save(add_note=False)
                logger.info(
                    'Build output %s marked as USED (consumed USED component)',
                    output.pk,
                )
            except Exception:
                logger.exception('Failed to mark build output as USED')

    Build.complete_build_output = wrapper


def _patch_complete_allocations():
    """Wrap Build.complete_allocations to mark outputs USED if any was USED.

    Catches the case where the entire build is completed (build-level allocations).
    """
    from build.models import Build

    original = Build.complete_allocations

    def wrapper(self, user):
        used_stock_pks = list(
            self.allocated_stock.filter(
                stock_item__status_custom_key=USED_CUSTOM_KEY
            ).values_list('stock_item_id', flat=True)
        )
        original(self, user)
        if not used_stock_pks:
            return
        try:
            used_state = InvenTreeCustomUserStateModel.objects.get(
                reference_status='StockStatus', key=USED_CUSTOM_KEY
            )
            from stock.models import StockItem

            outputs = StockItem.objects.filter(build=self, is_building=False)
            updated = 0
            for output in outputs:
                if output.status_custom_key != USED_CUSTOM_KEY:
                    output.status_custom_key = used_state.key
                    output.status = used_state.logical_key
                    output.save(add_note=False)
                    updated += 1
            if updated:
                logger.info(
                    'Marked %d build output(s) as USED for build %s',
                    updated, self,
                )
        except Exception:
            logger.exception('Failed to mark build outputs as USED')

    Build.complete_allocations = wrapper


_patch_complete_build_output()
_patch_complete_allocations()
