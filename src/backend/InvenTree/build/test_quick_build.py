"""Unit tests for quick builds (fractional assemblies without a build order)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from build.models import QuickBuild
from common.settings import set_global_setting
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import BomItem, Part
from stock.models import StockItem, StockLocation
from stock.status_codes import StockHistoryCode, StockStatus


class QuickBuildTest(InvenTreeAPITestCase):
    """Tests for producing and disassembling spliced rope via quick builds."""

    roles = ['build.add', 'build.change', 'build.view', 'stock.view']

    @classmethod
    def setUpTestData(cls):
        """Create a rope assembly: rope (m) + 0.8 m splice loss + 2 thimbles."""
        super().setUpTestData()

        # Thimbles are a fixed amount per rope (setup quantity only)
        set_global_setting('PART_BOM_ALLOW_ZERO_QUANTITY', True)

        cls.location = StockLocation.objects.create(name='Rope rack')
        cls.recovery = StockLocation.objects.create(name='Inspection')

        cls.rope = Part.objects.create(name='Dyneema 8mm', units='m', component=True)
        cls.thimble = Part.objects.create(name='Thimble 8mm', component=True)

        cls.spliced = Part.objects.create(
            name='Rope with splices - Dyneema 8mm',
            units='m',
            assembly=True,
            quick_build=True,
        )

        # Reload the tree fields assigned when each part was inserted
        for part in [cls.rope, cls.thimble, cls.spliced]:
            part.refresh_from_db()

        cls.rope_line = BomItem.objects.create(
            part=cls.spliced, sub_part=cls.rope, quantity=1, setup_quantity=0.8
        )
        cls.thimble_line = BomItem.objects.create(
            part=cls.spliced,
            sub_part=cls.thimble,
            raw_amount='0',
            quantity=0,
            setup_quantity=2,
        )

    def setUp(self):
        """Create fresh source stock for each test."""
        super().setUp()

        self.reel = StockItem.objects.create(
            part=self.rope, quantity=50, location=self.location
        )
        self.thimbles = StockItem.objects.create(
            part=self.thimble, quantity=10, location=self.location
        )

    def quick_build(self, quantity='1.8', expected_code=201, **kwargs):
        """Perform a quick build through the API."""
        data = {
            'part': self.spliced.pk,
            'quantity': quantity,
            'location': self.location.pk,
            'items': [
                {'bom_item': self.rope_line.pk, 'stock_item': self.reel.pk},
                {'bom_item': self.thimble_line.pk, 'stock_item': self.thimbles.pk},
            ],
            **kwargs,
        }

        return self.post(
            reverse('api-quick-build-create'), data, expected_code=expected_code
        )

    def test_requirements(self):
        """The requirements include the setup loss and suggest source stock."""
        response = self.post(
            reverse('api-quick-build-requirements'),
            {'part': self.spliced.pk, 'quantity': '1.8'},
            expected_code=200,
        )

        items = {item['bom_item']: item for item in response.data['items']}

        self.assertAlmostEqual(items[self.rope_line.pk]['required'], 2.6)
        self.assertEqual(items[self.rope_line.pk]['stock_item'], self.reel.pk)
        self.assertAlmostEqual(items[self.thimble_line.pk]['required'], 2)
        self.assertEqual(items[self.thimble_line.pk]['stock_item'], self.thimbles.pk)

    def test_requirements_prefers_remnant(self):
        """The smallest sufficient stock item is suggested."""
        remnant = StockItem.objects.create(
            part=self.rope, quantity=3, location=self.location
        )
        StockItem.objects.create(part=self.rope, quantity=2, location=self.location)

        response = self.post(
            reverse('api-quick-build-requirements'),
            {'part': self.spliced.pk, 'quantity': '1.8'},
            expected_code=200,
        )

        items = {item['bom_item']: item for item in response.data['items']}
        self.assertEqual(items[self.rope_line.pk]['stock_item'], remnant.pk)

    def test_quick_build(self):
        """A fractional quick build consumes rope + loss and thimbles."""
        response = self.quick_build(notes='Mooring line')

        self.assertEqual(response.data['reference'], 'QB-0001')
        self.assertAlmostEqual(response.data['quantity'], 1.8)
        self.assertFalse(response.data['disassembled'])

        quick_build = QuickBuild.objects.get(pk=response.data['pk'])

        self.reel.refresh_from_db()
        self.thimbles.refresh_from_db()
        self.assertEqual(self.reel.quantity, Decimal('47.4'))
        self.assertEqual(self.thimbles.quantity, 8)

        consumed = StockItem.objects.filter(consumed_by_quick_build=quick_build)
        self.assertEqual(consumed.count(), 2)

        for item in consumed:
            self.assertFalse(item.in_stock)
            self.assertIsNone(item.location)
            self.assertTrue(
                item.tracking_info.filter(
                    tracking_type=StockHistoryCode.QUICK_BUILD_CONSUMED
                ).exists()
            )

        self.assertEqual(consumed.get(part=self.rope).quantity, Decimal('2.6'))

        # Consumed stock does not count towards available stock
        self.assertEqual(self.rope.total_stock, Decimal('47.4'))

        rope_line = quick_build.lines.get(part=self.rope)
        self.assertEqual(rope_line.quantity, Decimal('2.6'))
        self.assertEqual(rope_line.loss, Decimal('0.8'))

        output = quick_build.output
        self.assertEqual(output.part, self.spliced)
        self.assertEqual(output.quantity, Decimal('1.8'))
        self.assertEqual(output.location, self.location)
        self.assertTrue(output.in_stock)
        self.assertEqual(response.data['output'], output.pk)

        # The output is a discrete item and cannot be split or merged
        with self.assertRaises(ValidationError):
            output.splitStock(1, None, None)

        other = QuickBuild.objects.get(pk=self.quick_build().data['pk']).output
        self.assertFalse(output.can_merge(other))

        # The quick build is listed
        response = self.get(reverse('api-quick-build-list'), expected_code=200)
        self.assertEqual(len(response.data), 2)

    def test_quick_build_insufficient_stock(self):
        """Nothing is consumed when the source stock does not cover the requirement."""
        self.reel.quantity = 2
        self.reel.save()

        response = self.quick_build(expected_code=400)
        self.assertIn(
            'Insufficient stock', str(response.data['items'][0]['stock_item'])
        )
        self.assertEqual(response.data['items'][1], {})

        self.assertFalse(QuickBuild.objects.exists())
        self.reel.refresh_from_db()
        self.thimbles.refresh_from_db()
        self.assertEqual(self.reel.quantity, 2)
        self.assertEqual(self.thimbles.quantity, 10)

    def test_quick_build_invalid(self):
        """Invalid parts, quantities and source stock are rejected."""
        self.quick_build(quantity='0', expected_code=400)

        # Wrong part for the BOM line
        response = self.quick_build(
            items=[
                {'bom_item': self.rope_line.pk, 'stock_item': self.thimbles.pk},
                {'bom_item': self.thimble_line.pk, 'stock_item': self.thimbles.pk},
            ],
            expected_code=400,
        )
        self.assertIn(
            'does not match BOM part', str(response.data['items'][0]['stock_item'])
        )

        # Missing (required) BOM line
        response = self.quick_build(
            items=[{'bom_item': self.rope_line.pk, 'stock_item': self.reel.pk}],
            expected_code=400,
        )
        self.assertIn('Thimble 8mm', str(response.data['non_field_errors']))

        # Part not enabled for quick builds
        self.spliced.quick_build = False
        self.spliced.save()
        self.quick_build(expected_code=400)

        self.assertFalse(QuickBuild.objects.exists())

    def test_quick_build_flag_requires_assembly(self):
        """Only untracked assemblies can be quick built."""
        part = Part(name='Not an assembly', quick_build=True, assembly=False)

        with self.assertRaises(ValidationError):
            part.full_clean()

    def test_disassemble(self):
        """Disassembly recovers the full consumed quantity, including the splices."""
        quick_build = QuickBuild.objects.get(pk=self.quick_build().data['pk'])
        output = quick_build.output
        url = reverse('api-build-output-disassemble-from-stock')

        # The output is offered for disassembly
        response = self.get(
            reverse('api-stock-list'),
            {'has_build': True, 'in_stock': True},
            expected_code=200,
        )
        self.assertIn(output.pk, [item['pk'] for item in response.data])

        preview = self.post(
            url + '?preview=true', {'output': output.pk}, expected_code=200
        )
        self.assertEqual(len(preview.data['items']), 2)

        quantities = {item['part']: item['quantity'] for item in preview.data['items']}
        self.assertAlmostEqual(quantities[self.rope.pk], 2.6)
        self.assertAlmostEqual(quantities[self.thimble.pk], 2)

        # A disposition is required for every consumed item
        self.post(
            url,
            {'output': output.pk, 'items': preview.data['items'][:1]},
            expected_code=400,
        )

        items = [
            {
                'stock_item': item['stock_item'],
                'location': self.recovery.pk,
                'status': StockStatus.QUARANTINED.value,
            }
            for item in preview.data['items']
        ]

        response = self.post(
            url,
            {'output': output.pk, 'items': items, 'notes': 'Splices undone'},
            expected_code=200,
        )
        self.assertEqual(response.data['status'], StockStatus.DISASSEMBLED.value)

        quick_build.refresh_from_db()
        output.refresh_from_db()
        self.assertTrue(quick_build.disassembled)
        self.assertIsNotNone(quick_build.disassembled_date)
        self.assertEqual(output.status, StockStatus.DISASSEMBLED)
        self.assertFalse(output.in_stock)
        self.assertTrue(
            output.tracking_info.filter(
                tracking_type=StockHistoryCode.QUICK_BUILD_DISASSEMBLED
            ).exists()
        )

        self.assertFalse(
            StockItem.objects.filter(consumed_by_quick_build=quick_build).exists()
        )

        recovered = StockItem.objects.filter(location=self.recovery)
        self.assertEqual(recovered.count(), 2)
        self.assertEqual(recovered.get(part=self.rope).quantity, Decimal('2.6'))
        self.assertEqual(recovered.get(part=self.thimble).quantity, 2)

        for item in recovered:
            self.assertEqual(item.status, StockStatus.QUARANTINED)

        # Listed in the Disassembly panel
        response = self.get(
            reverse('api-stock-list'), {'disassembled': True}, expected_code=200
        )
        self.assertEqual([item['pk'] for item in response.data], [output.pk])

        # Cannot be disassembled twice
        self.post(url + '?preview=true', {'output': output.pk}, expected_code=400)

    def test_disassemble_requires_untouched_output(self):
        """An output which has been used elsewhere cannot be disassembled."""
        quick_build = QuickBuild.objects.get(pk=self.quick_build().data['pk'])
        output = quick_build.output

        output.take_stock(Decimal('0.5'), None)

        with self.assertRaises(ValidationError):
            quick_build.validate_disassemble()
