"""Assign the dedicated status to previously disassembled stock items."""

from django.db import migrations

REJECTED_STATUS = 65
DISASSEMBLED_STATUS = 80
BUILD_DISASSEMBLED_HISTORY = 58


def mark_disassembled_stock(apps, schema_editor):
    """Apply the disassembled status to outputs processed by the feature."""
    StockItem = apps.get_model('stock', 'StockItem')
    StockItem.objects.filter(
        status=REJECTED_STATUS,
        tracking_info__tracking_type=BUILD_DISASSEMBLED_HISTORY,
    ).update(status=DISASSEMBLED_STATUS)


def restore_rejected_status(apps, schema_editor):
    """Restore the previous status when reversing this migration."""
    StockItem = apps.get_model('stock', 'StockItem')
    StockItem.objects.filter(
        status=DISASSEMBLED_STATUS,
        tracking_info__tracking_type=BUILD_DISASSEMBLED_HISTORY,
    ).update(status=REJECTED_STATUS)


class Migration(migrations.Migration):
    """Update stock outputs which already have disassembly history."""

    dependencies = [('stock', '0123_remove_stockitem_review_needed')]

    operations = [
        migrations.RunPython(mark_disassembled_stock, restore_rejected_status),
    ]
