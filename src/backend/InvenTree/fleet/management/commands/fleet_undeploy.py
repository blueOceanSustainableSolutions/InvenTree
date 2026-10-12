"""Put deployments which the old stock sync opened back to READY.

Shipping is not deploying. Before this rule, the stock sync marked a fleet
device as deployed as soon as it had a customer (e.g. shipped on a sales
order). Without arguments this command lists the DEPLOYED deployments which
the fleet Deploy action did not deploy (no FLEET_DEPLOYED stock entry); give
their references to mark them as not deployed. Use --dry-run first.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from fleet.models import Deployment
from fleet.services.deployment import deployed_by_fleet, undo_deploy
from fleet.services.stock_sync import DryRunError, describe
from fleet.status_codes import DeploymentStatus


class Command(BaseCommand):
    """List, or mark as not deployed, the deployments opened from a customer."""

    help = str(_('Mark deployments opened by a shipment as not deployed'))

    def add_arguments(self, parser):
        """Deployment references, and the --dry-run flag."""
        parser.add_argument(
            'references', nargs='*', help='Deployments to mark as not deployed'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change, without saving anything',
        )

    def handle(self, *args, **options):
        """List the candidates, or undo the given deployments."""
        references = options['references']

        if not references:
            for deployment in Deployment.objects.filter(
                status=DeploymentStatus.DEPLOYED.value
            ).select_related('device', 'device__part', 'site'):
                if not deployed_by_fleet(deployment):
                    customer = deployment.client.name if deployment.client else '-'
                    self.stdout.write(
                        f'Candidate: {describe(deployment)} (customer {customer})'
                    )

            return

        deployments = []

        for reference in references:
            deployment = Deployment.objects.filter(reference=reference).first()

            if deployment is None:
                raise CommandError(f'No deployment {reference}')

            deployments.append(deployment)

        try:
            with transaction.atomic():
                for deployment in deployments:
                    try:
                        undo_deploy(deployment)
                    except ValidationError as exc:
                        raise CommandError(
                            f'{deployment.reference}: {"; ".join(exc.messages)}'
                        )

                    self.stdout.write(f'Not deployed: {describe(deployment)}')

                if options['dry_run']:
                    raise DryRunError
        except DryRunError:
            self.stdout.write('Dry run: nothing was saved.')
