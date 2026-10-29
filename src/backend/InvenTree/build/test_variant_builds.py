"""Tests for building variants of a template part instead of the template itself."""

from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import BomItem, Part
from plugin.builtin.events.auto_create_builds import AutoCreateBuildsPlugin
from stock.models import StockItem

from .models import Build, BuildItem, BuildLine


class VariantBuildTests(InvenTreeAPITestCase):
    """A template is never built: build orders are created for its variants."""

    roles = ['build.view', 'build.add', 'build.change', 'build.delete']

    @classmethod
    def setUpTestData(cls):
        """Create a product whose BOM needs a template sub-assembly."""
        super().setUpTestData()
        cls.product = Part.objects.create(name='Buoy', assembly=True)
        cls.template = Part.objects.create(
            name='Sensor', assembly=True, is_template=True
        )
        cls.variant_a = Part.objects.create(
            name='Sensor A', assembly=True, variant_of=cls.template
        )
        cls.variant_b = Part.objects.create(
            name='Sensor B', assembly=True, variant_of=cls.template
        )
        cls.tracked = Part.objects.create(
            name='Sensor T', assembly=True, trackable=True, variant_of=cls.template
        )
        cls.unrelated = Part.objects.create(name='Pump', assembly=True)
        cls.bom_item = BomItem.objects.create(
            part=cls.product, sub_part=cls.template, quantity=5, allow_variants=False
        )
        cls.build = Build.objects.create(
            part=cls.product, reference='BO-9200', quantity=1
        )

    @property
    def line(self):
        """The build line for the template sub-assembly."""
        return BuildLine.objects.get(build=self.build, bom_item=self.bom_item)

    def build_variants(self, items, expected_code=201, **extra):
        """Request build orders for variants of the template."""
        return self.post(
            reverse('api-build-variant-create'),
            {'part': self.template.pk, 'items': items, **extra},
            expected_code=expected_code,
        )

    def test_template_cannot_be_built(self):
        """Creating a build order for a template part is refused."""
        response = self.post(
            reverse('api-build-list'),
            {'part': self.template.pk, 'reference': 'BO-9201', 'quantity': 1},
            expected_code=400,
        )
        self.assertIn('part', response.data)
        self.assertFalse(Build.objects.filter(part=self.template).exists())

    def test_build_variants_for_line(self):
        """Each variant gets its own child build order, and the line accepts variants."""
        self.assertFalse(self.line.allow_variants)

        response = self.build_variants(
            [
                {'part': self.variant_a.pk, 'quantity': 3},
                {'part': self.variant_b.pk, 'quantity': 2},
                {'part': self.tracked.pk, 'quantity': None},
            ],
            build_line=self.line.pk,
            title='Sensors for the buoy',
        )

        self.assertEqual(len(response.data), 2)
        children = Build.objects.filter(parent=self.build)
        self.assertEqual(
            {(b.part_id, int(b.quantity)) for b in children},
            {(self.variant_a.pk, 3), (self.variant_b.pk, 2)},
        )
        self.assertTrue(all(b.title == 'Sensors for the buoy' for b in children))

        # The BOM itself is not changed, only this build order's line
        self.bom_item.refresh_from_db()
        self.assertFalse(self.bom_item.allow_variants)
        self.assertTrue(self.line.allow_variants)

    def test_variant_stock_allocates_to_line(self):
        """Once variants are allowed on the line, their stock can be allocated."""
        stock = StockItem.objects.create(part=self.variant_a, quantity=5)
        url = reverse('api-build-allocate', kwargs={'pk': self.build.pk})
        allocation = {
            'items': [
                {'build_line': self.line.pk, 'stock_item': stock.pk, 'quantity': 3}
            ]
        }

        self.post(url, allocation, expected_code=400)
        listed = self.get(reverse('api-stock-list'), {'build_line': self.line.pk})
        self.assertEqual(len(listed.data), 0)

        self.build_variants(
            [{'part': self.variant_a.pk, 'quantity': 1}], build_line=self.line.pk
        )

        listed = self.get(reverse('api-stock-list'), {'build_line': self.line.pk})
        self.assertEqual([row['pk'] for row in listed.data], [stock.pk])

        self.post(url, allocation, expected_code=201)
        self.assertEqual(BuildItem.objects.get(build_line=self.line).quantity, 3)

    def test_trackable_variant_one_build_per_unit(self):
        """A trackable variant is built one unit per build order."""
        self.build_variants([{'part': self.tracked.pk, 'quantity': 3}])

        builds = Build.objects.filter(part=self.tracked)
        self.assertEqual(builds.count(), 3)
        self.assertTrue(all(b.quantity == 1 for b in builds))
        self.assertEqual(len({b.reference for b in builds}), 3)

        response = self.build_variants(
            [{'part': self.tracked.pk, 'quantity': 1.5}], expected_code=400
        )
        self.assertIn('quantity', response.data['items'][0])

    def test_invalid_requests(self):
        """Only variants of the template, with a quantity, can be built."""
        response = self.build_variants(
            [{'part': self.unrelated.pk, 'quantity': 1}], expected_code=400
        )
        self.assertIn('part', response.data['items'][0])

        self.build_variants([{'part': self.variant_a.pk, 'quantity': 0}], 400)

        # Only templates are accepted as the part to build variants of
        self.post(
            reverse('api-build-variant-create'),
            {'part': self.variant_a.pk, 'items': []},
            expected_code=400,
        )

        # A build line must require the template
        other = Build.objects.create(part=self.unrelated, reference='BO-9202')
        line = BuildLine.objects.create(build=other, custom_part=self.unrelated)
        response = self.build_variants(
            [{'part': self.variant_a.pk, 'quantity': 1}],
            expected_code=400,
            build_line=line.pk,
        )
        self.assertIn('build_line', response.data)

        self.assertFalse(Build.objects.filter(part__variant_of=self.template).exists())

    def test_auto_create_skips_templates(self):
        """The auto-create plugin leaves template sub-assemblies to a person."""
        AutoCreateBuildsPlugin().process_build(self.build)
        self.assertFalse(Build.objects.filter(parent=self.build).exists())
