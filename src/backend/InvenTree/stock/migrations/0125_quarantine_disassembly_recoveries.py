"""Quarantine stock recreated by disassemblies performed before this change."""

from django.db import migrations

QUARANTINED_STATUS = 75
DISASSEMBLY_RECOVERED_HISTORY = 59


def quarantine_recovered_stock(apps, schema_editor):
    """Quarantine items carrying an explicit disassembly recovery record."""
    StockItem = apps.get_model('stock', 'StockItem')
    StockItem.objects.filter(
        tracking_info__tracking_type=DISASSEMBLY_RECOVERED_HISTORY
    ).update(status=QUARANTINED_STATUS)


class Migration(migrations.Migration):
    """Update stock explicitly recreated by earlier disassembly operations."""

    dependencies = [('stock', '0124_mark_disassembled_stock')]

    operations = [migrations.RunPython(quarantine_recovered_stock, migrations.RunPython.noop)]
