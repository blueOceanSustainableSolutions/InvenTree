"""Tests for single-unit build orders of trackable parts.

A trackable assembly is built one unit per build order, so tracked stock can be
allocated against the whole build order and goes into its only output.
"""

from django.core.exceptions import ValidationError
from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import BomItem, Part
from stock.models import StockItem

from .models import Build, BuildItem


class SingleUnitBuildTest(InvenTreeAPITestCase):
    """Quantity limit and build-level allocation of tracked parts."""

    roles = ['build.view', 'build.add', 'build.change', 'build.delete']

    @classmethod
    def setUpTestData(cls):
        """Create a trackable assembly with a tracked and an untracked component."""
        super().setUpTestData()

        cls.product = Part.objects.create(
            name='Single product', assembly=True, trackable=True
        )
        cls.untracked_product = Part.objects.create(
            name='Single untracked product', assembly=True
        )
        cls.tracked = Part.objects.create(
            name='Single tracked', component=True, trackable=True
        )
        cls.screw = Part.objects.create(name='Single screw', component=True)

        cls.tracked_bom = BomItem.objects.create(
            part=cls.product, sub_part=cls.tracked, quantity=1
        )
        cls.screw_bom = BomItem.objects.create(
            part=cls.product, sub_part=cls.screw, quantity=4
        )

    def setUp(self):
        """Create a single-unit build order with one output and some stock."""
        super().setUp()

        self.build = Build.objects.create(
            part=self.product, reference='BO-9960', quantity=1
        )
        self.tracked_line = self.build.build_lines.get(bom_item=self.tracked_bom)
        self.screw_line = self.build.build_lines.get(bom_item=self.screw_bom)

        self.spotter = StockItem.objects.create(
            part=self.tracked, quantity=1, serial='SP-1'
        )
        self.screws = StockItem.objects.create(part=self.screw, quantity=100)

    def add_output(self, serial):
        """Create an incomplete build output."""
        return StockItem.objects.create(
            part=self.product,
            quantity=1,
            serial=serial,
            build=self.build,
            is_building=True,
        )

    def allocate(self, line, stock_item, quantity=1, expected_code=201, **extra):
        """Allocate stock against the whole build order (no output given)."""
        return self.post(
            reverse('api-build-allocate', kwargs={'pk': self.build.pk}),
            {
                'items': [
                    {
                        'build_line': line.pk,
                        'stock_item': stock_item.pk,
                        'quantity': quantity,
                        **extra,
                    }
                ]
            },
            expected_code=expected_code,
        )

    def test_trackable_quantity_limited_to_one(self):
        """A new build order of a trackable part cannot be for more than one unit."""
        url = reverse('api-build-list')

        response = self.post(
            url,
            {'part': self.product.pk, 'reference': 'BO-9961', 'quantity': 2},
            expected_code=400,
        )
        self.assertIn('quantity', response.data)

        self.post(
            url,
            {'part': self.product.pk, 'reference': 'BO-9962', 'quantity': 1},
            expected_code=201,
        )

        # Untracked assemblies keep any quantity
        self.post(
            url,
            {'part': self.untracked_product.pk, 'reference': 'BO-9963', 'quantity': 5},
            expected_code=201,
        )

    def test_quantity_cannot_be_raised(self):
        """An existing single-unit build order cannot be raised above one."""
        self.patch(
            reverse('api-build-detail', kwargs={'pk': self.build.pk}),
            {'quantity': 3},
            expected_code=400,
        )

    def test_existing_multi_unit_build_still_editable(self):
        """Older multi-unit build orders can still be edited."""
        build = Build.objects.create(
            part=self.product, reference='BO-9964', quantity=2
        )
        build.title = 'Edited'
        build.full_clean()

        build.quantity = 3
        with self.assertRaises(ValidationError):
            build.full_clean()

    def test_tracked_allocation_goes_to_only_output(self):
        """Tracked stock allocated to the build order is installed into its output."""
        output = self.add_output('PROD-1')

        self.allocate(self.tracked_line, self.spotter)

        item = BuildItem.objects.get(build_line=self.tracked_line)
        self.assertEqual(item.install_into, output)
        self.assertEqual(item.stock_item, self.spotter)

        # Untracked stock stays unassigned to any output
        self.allocate(self.screw_line, self.screws, quantity=4)
        self.assertIsNone(
            BuildItem.objects.get(build_line=self.screw_line).install_into
        )

    def test_tracked_allocation_needs_exactly_one_output(self):
        """Without exactly one incomplete output, the allocation is refused."""
        response = self.allocate(self.tracked_line, self.spotter, expected_code=400)
        self.assertIn('output', str(response.data))

        self.add_output('PROD-1')
        self.add_output('PROD-2')

        self.allocate(self.tracked_line, self.spotter, expected_code=400)
        self.assertFalse(BuildItem.objects.filter(build_line=self.tracked_line).exists())

    def test_explicit_output_still_works(self):
        """Allocating against a chosen output is unchanged."""
        self.add_output('PROD-1')
        other = self.add_output('PROD-2')

        self.allocate(self.tracked_line, self.spotter, output=other.pk)
        self.assertEqual(
            BuildItem.objects.get(build_line=self.tracked_line).install_into, other
        )

    def test_deallocate_tracked_line_from_build(self):
        """Deallocating a tracked line from the build order clears its output allocation."""
        self.add_output('PROD-1')
        self.allocate(self.tracked_line, self.spotter)
        self.allocate(self.screw_line, self.screws, quantity=4)

        self.post(
            reverse('api-build-unallocate', kwargs={'pk': self.build.pk}),
            {'build_line': self.tracked_line.pk},
            expected_code=201,
        )

        self.assertFalse(BuildItem.objects.filter(build_line=self.tracked_line).exists())
        self.assertTrue(BuildItem.objects.filter(build_line=self.screw_line).exists())
