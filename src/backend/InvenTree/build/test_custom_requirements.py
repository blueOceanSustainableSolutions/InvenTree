"""Tests for requirements which apply to one build order only."""

from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part
from stock.models import StockItem

from .models import Build, BuildItem, BuildLine


class CustomBuildRequirementTests(InvenTreeAPITestCase):
    """Exercise custom requirements through the normal allocation workflow."""

    roles = ['build.view', 'build.add', 'build.change', 'build.delete']

    @classmethod
    def setUpTestData(cls):
        """Create a build whose product BOM is intentionally empty."""
        super().setUpTestData()
        cls.product = Part.objects.create(name='Configurable product', assembly=True)
        cls.component = Part.objects.create(name='Order-specific component')
        cls.other_component = Part.objects.create(name='Wrong component')
        cls.build = Build.objects.create(
            part=cls.product, reference='BO-9100', quantity=1
        )
        cls.stock = StockItem.objects.create(part=cls.component, quantity=5)
        cls.wrong_stock = StockItem.objects.create(part=cls.other_component, quantity=5)

    def create_requirement(self, quantity=2, expected_code=201):
        """Create one build-only requirement through the public API."""
        return self.post(
            reverse('api-build-line-list'),
            {
                'build': self.build.pk,
                'custom_part': self.component.pk,
                'quantity': quantity,
            },
            expected_code=expected_code,
        )

    def test_create_list_allocate_and_consume(self):
        """A custom line participates in standard allocation and consumption."""
        response = self.create_requirement()
        line = BuildLine.objects.get(pk=response.data['pk'])
        self.assertTrue(line.is_custom)
        self.assertEqual(line.part, self.component)
        self.assertFalse(self.product.bom_items.exists())

        listed = self.get(
            reverse('api-build-line-list'),
            {'build': self.build.pk, 'part_detail': True},
        )
        row = listed.data[0]
        self.assertTrue(row['is_custom'])
        self.assertEqual(row['part'], self.component.pk)
        self.assertEqual(row['available_stock'], 5)
        part_data = self.get(
            reverse('api-part-detail', kwargs={'pk': self.component.pk})
        ).data
        self.assertEqual(part_data['required_for_build_orders'], 2)

        allocate_url = reverse('api-build-allocate', kwargs={'pk': self.build.pk})
        self.post(
            allocate_url,
            {
                'items': [
                    {'build_line': line.pk, 'stock_item': self.stock.pk, 'quantity': 2}
                ]
            },
            expected_code=201,
        )
        allocation = BuildItem.objects.get(build_line=line)

        self.post(
            reverse('api-build-consume', kwargs={'pk': self.build.pk}),
            {'items': [{'build_item': allocation.pk, 'quantity': 2}]},
            expected_code=200,
        )
        line.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(line.consumed, 2)
        self.assertTrue(
            StockItem.objects.filter(
                part=self.component, consumed_by=self.build, quantity=2
            ).exists()
        )

    def test_allocation_must_match_custom_requirement(self):
        """Unrelated stock cannot be allocated against a custom requirement."""
        line = BuildLine.objects.get(pk=self.create_requirement().data['pk'])
        self.post(
            reverse('api-build-allocate', kwargs={'pk': self.build.pk}),
            {
                'items': [
                    {
                        'build_line': line.pk,
                        'stock_item': self.wrong_stock.pk,
                        'quantity': 1,
                    }
                ]
            },
            expected_code=400,
        )
        self.assertFalse(BuildItem.objects.filter(build_line=line).exists())

    def test_custom_requirement_can_allow_variants(self):
        """A custom requirement can be configured to accept its variants."""
        variant = Part.objects.create(
            name='Order-specific component variant', variant_of=self.component
        )
        variant_stock = StockItem.objects.create(part=variant, quantity=2)

        response = self.post(
            reverse('api-build-line-list'),
            {
                'build': self.build.pk,
                'custom_part': self.component.pk,
                'quantity': 2,
                'allow_variants': True,
            },
        )
        line = BuildLine.objects.get(pk=response.data['pk'])

        self.post(
            reverse('api-build-allocate', kwargs={'pk': self.build.pk}),
            {
                'items': [
                    {
                        'build_line': line.pk,
                        'stock_item': variant_stock.pk,
                        'quantity': 2,
                    }
                ]
            },
            expected_code=201,
        )

    def test_auto_allocate_custom_requirement(self):
        """Normal automatic allocation includes build-specific required parts."""
        line = BuildLine.objects.get(pk=self.create_requirement().data['pk'])
        self.build.auto_allocate_untracked_stock(interchangeable=True)
        allocation = BuildItem.objects.get(build_line=line)
        self.assertEqual(allocation.stock_item, self.stock)
        self.assertEqual(allocation.quantity, 2)

    def test_edit_and_remove_unused_requirement(self):
        """An empty build can accept a new requirement after its final line is removed."""
        line = BuildLine.objects.get(pk=self.create_requirement().data['pk'])
        detail = reverse('api-build-line-detail', kwargs={'pk': line.pk})
        self.patch(detail, {'quantity': 3})
        line.refresh_from_db()
        self.assertEqual(line.quantity, 3)
        self.delete(detail, expected_code=204)
        self.assertFalse(BuildLine.objects.filter(pk=line.pk).exists())
        self.assertFalse(self.product.bom_items.exists())

        # Removing every requirement must not prevent adding one back to this build.
        response = self.create_requirement(quantity=1)
        self.assertEqual(response.data['part'], self.component.pk)
        self.assertTrue(BuildLine.objects.filter(pk=response.data['pk']).exists())

    def test_duplicate_and_destructive_edits_are_rejected(self):
        """A requirement stays consistent with its allocation and consumption."""
        line = BuildLine.objects.get(pk=self.create_requirement().data['pk'])
        self.create_requirement(quantity=1, expected_code=400)

        BuildItem.objects.create(build_line=line, stock_item=self.stock, quantity=1)
        detail = reverse('api-build-line-detail', kwargs={'pk': line.pk})
        self.patch(detail, {'quantity': 0}, expected_code=400)
        self.delete(detail, expected_code=400)
