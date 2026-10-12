"""Keep the fleet rows in step with the stock (every fleet unit has its row).

Runs the same sync as the scheduled pipeline task. Use --dry-run first (e.g.
before the first run on production): it reports what would be opened, closed
or changed, and rolls back.
"""

from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _

from fleet.services.stock_sync import sync_stock_deployments


class Command(BaseCommand):
    """Close, update or add fleet rows from the stock."""

    help = str(_('Sync the fleet deployment list with the stock'))

    def add_arguments(self, parser):
        """Add the --dry-run flag."""
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change, without saving anything',
        )

    def handle(self, *args, **options):
        """Run the sync (rolled back with --dry-run)."""
        dry_run = options['dry_run']
        summary = sync_stock_deployments(dry_run=dry_run)

        for key, label in [
            ('opened', 'Open'),
            ('closed', 'Close'),
            ('updated', 'Update'),
            ('available', 'Available'),
            ('cancelled', 'Cancel'),
            ('errors', 'Error'),
        ]:
            for line in summary[key]:
                self.stdout.write(f'{label}: {line}')

        counts = (
            f'{len(summary["opened"])} opened, {len(summary["closed"])} closed, '
            f'{len(summary["updated"])} updated, '
            f'{len(summary["available"])} available, '
            f'{len(summary["cancelled"])} cancelled, {len(summary["errors"])} errors'
        )

        if dry_run:
            self.stdout.write(f'Dry run: {counts}. Nothing was saved.')
        else:
            self.stdout.write(f'Done: {counts}.')
