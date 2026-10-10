"""Install the default fleet report templates (e.g. the service report).

The templates live in fleet/templates/fleet/. Running the command again keeps
an existing template (which may have been edited in the admin), unless
--update is given.
"""

from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils.translation import gettext_lazy as _

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / 'templates' / 'fleet'

# Default fleet report templates
REPORTS = [
    {
        'file': 'fleet_service_report.html',
        'name': 'Fleet Service Report',
        'description': 'Service report of a fleet maintenance task',
        'model_type': 'maintenancetask',
        'filename_pattern': 'ServiceReport-{{ reference }}.pdf',
    },
    {
        'file': 'fleet_trip_report.html',
        'name': 'Fleet Trip Report',
        'description': 'Report of a fleet field trip: tasks, actions and parts used',
        'model_type': 'fieldtrip',
        'filename_pattern': 'TripReport-{{ reference }}.pdf',
    },
]


def install_reports(update: bool = False) -> list[str]:
    """Create (or with update=True, refresh) the fleet report templates.

    Returns:
        One line per template: created, updated or kept
    """
    from report.models import ReportTemplate

    lines = []

    for spec in REPORTS:
        spec = dict(spec)
        filename = spec.pop('file')
        content = (TEMPLATE_DIR / filename).read_text(encoding='utf-8')

        template = ReportTemplate.objects.filter(
            name=spec['name'], model_type=spec['model_type']
        ).first()

        if template is None:
            ReportTemplate.objects.create(
                **spec, template=ContentFile(content, filename)
            )
            lines.append(f'Created: {spec["name"]}')
        elif update:
            template.template = ContentFile(content, filename)
            template.description = spec['description']
            template.filename_pattern = spec['filename_pattern']
            template.save()
            lines.append(f'Updated: {spec["name"]}')
        else:
            lines.append(f'Kept: {spec["name"]} (use --update to replace it)')

    return lines


class Command(BaseCommand):
    """Install the default fleet report templates."""

    help = str(_('Install the default fleet report templates'))

    def add_arguments(self, parser):
        """Add the --update flag."""
        parser.add_argument(
            '--update',
            action='store_true',
            help='Replace the template file of reports which already exist',
        )

    def handle(self, *args, **options):
        """Create or update the templates."""
        for line in install_reports(update=options['update']):
            self.stdout.write(line)
