"""Tests for carried over stock and moving consumed stock between build orders.

Stock consumed by one build order can be handed over to another one (e.g. the
fleet app moves the mooring of a swapped device). Until then the receiving
line counts the pending quantity as covered (BuildLine.carried_over).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from build.status_codes import BuildStatus
from InvenTree.unit_test import InvenTreeTestCase
from part.models import BomItem, Part
from stock.models import StockItem, StockItemTracking
from stock.status_codes import StockHistoryCode

from .models import Build, BuildItem, BuildLine


class CarriedOverTest(InvenTreeTestCase):
    """Pending carried over stock counts as allocated."""

    @classmethod
    def setUpTestData(cls):
        """A product with a rope (template, variants allowed) and a chain."""
        super().setUpTestData()

        cls.product = Part.objects.create(name='Buoy system', assembly=True)
        cls.rope = Part.objects.create(name='Rope', component=True, is_template=True)
        cls.spliced = Part.objects.create(
            name='Rope with splices', component=True, variant_of=cls.rope
        )
        cls.chain = Part.objects.create(name='Chain', component=True)
        cls.anchor = Part.objects.create(name='Anchor', component=True)

        BomItem.objects.create(
            part=cls.product, sub_part=cls.rope, quantity=1, allow_variants=True
        )
        BomItem.objects.create(part=cls.product, sub_part=cls.chain, quantity=4)

    def setUp(self):
        """A build order in production, with its output completed."""
        super().setUp()

        self.build = Build.objects.create(
            part=self.product, reference='BO-9960', quantity=1
        )
        self.build.issue_build()

        output = self.build.create_build_output(1).first()
        self.build.complete_build_output(output, self.user)
        self.build.refresh_from_db()

        self.chain_line = self.build.build_lines.get(bom_item__sub_part=self.chain)
        self.rope_line = self.build.build_lines.get(bom_item__sub_part=self.rope)

    def carry(self, line, quantity):
        """Cover a line with carried over stock."""
        BuildLine.objects.filter(pk=line.pk).update(carried_over=quantity)
        line.refresh_from_db()

    def test_covered(self):
        """Carried over stock covers a line, as allocated stock does."""
        self.assertFalse(self.build.can_complete)
        self.assertEqual(self.chain_line.unallocated_quantity(), 4)

        # Part of the chain
        self.carry(self.chain_line, 3)
        self.assertEqual(self.chain_line.required_quantity(), 1)
        self.assertEqual(self.chain_line.unallocated_quantity(), 1)
        self.assertFalse(self.chain_line.is_fully_allocated())

        stock = StockItem.objects.create(part=self.chain, quantity=10)
        BuildItem.objects.create(
            build_line=self.chain_line, stock_item=stock, quantity=1
        )
        self.assertTrue(self.chain_line.is_fully_allocated())
        self.assertFalse(self.chain_line.is_overallocated())

        # Consumed quantities are not always in BOM units: 529 m of rope
        # against a BOM quantity of 1 covers the line
        self.carry(self.rope_line, 529)
        self.assertEqual(self.rope_line.required_quantity(), 0)
        self.assertTrue(self.rope_line.is_fully_allocated())

        self.assertFalse(self.build.unallocated_lines().exists())
        self.assertTrue(self.build.is_fully_allocated(tracked=False))
        self.assertFalse(self.build.is_overallocated())
        self.assertTrue(self.build.can_complete)

        # Allocating stock to a covered line over-allocates it
        BuildItem.objects.create(
            build_line=self.chain_line, stock_item=stock, quantity=1
        )
        self.assertTrue(self.chain_line.is_overallocated())
        self.assertTrue(self.build.is_overallocated())

    def test_move_consumed_stock(self):
        """Consumed stock moves to another build order, with its line quantities."""
        source = Build.objects.create(
            part=self.product, reference='BO-9961', quantity=1
        )
        BuildLine.objects.create(build=source, custom_part=self.anchor, quantity=1)

        rope = StockItem.objects.create(
            part=self.spliced, quantity=529, consumed_by=source
        )
        chain = StockItem.objects.create(
            part=self.chain, quantity=4, consumed_by=source
        )
        anchor = StockItem.objects.create(
            part=self.anchor, quantity=1, consumed_by=source
        )

        for item in [rope, chain, anchor]:
            line = source.match_build_line(item.part)
            line.consumed += item.quantity
            line.save()

        moved = source.move_consumed_stock(
            self.build,
            [(rope, Decimal(300), self.rope_line), (chain, 4, None), (anchor, 1, None)],
            user=self.user,
            notes='Handed over',
            tracking_code=StockHistoryCode.BUILD_ORDER_SPLIT,
            deltas={'note': 'test'},
            create_lines=True,
        )

        # The rope was split: 300 move, 229 stay
        rope.refresh_from_db()
        self.assertEqual(rope.quantity, 229)
        self.assertEqual(rope.consumed_by, source)

        moved_rope, quantity, line = moved[0]
        self.assertNotEqual(moved_rope.pk, rope.pk)
        self.assertEqual(
            (moved_rope.quantity, quantity, line), (300, 300, self.rope_line)
        )
        self.assertEqual(moved_rope.consumed_by, self.build)

        entry = StockItemTracking.objects.get(
            item=moved_rope, tracking_type=StockHistoryCode.BUILD_ORDER_SPLIT.value
        )
        self.assertEqual(
            entry.deltas,
            {'note': 'test', 'buildorder': self.build.pk, 'quantity': 300.0},
        )

        # Whole rows move as they are; a part without a line gets one
        chain.refresh_from_db()
        anchor.refresh_from_db()
        self.assertEqual(chain.consumed_by, self.build)
        self.assertEqual(anchor.consumed_by, self.build)

        custom = self.build.build_lines.get(custom_part=self.anchor)
        self.assertEqual((custom.quantity, custom.consumed), (1, 1))

        self.rope_line.refresh_from_db()
        self.chain_line.refresh_from_db()
        self.assertEqual(self.rope_line.consumed, 300)
        self.assertEqual(self.chain_line.consumed, 4)

        self.assertEqual(source.match_build_line(self.spliced).consumed, 229)
        self.assertEqual(source.match_build_line(self.chain).consumed, 0)
        self.assertEqual(source.match_build_line(self.anchor).consumed, 0)

        # Only stock consumed by the source, within its quantity
        loose = StockItem.objects.create(part=self.chain, quantity=4)

        for item, quantity in [(loose, 1), (rope, 230)]:
            with self.assertRaises(ValidationError):
                source.move_consumed_stock(self.build, [(item, quantity, None)])

    def test_consume_stock_item(self):
        """Stock consumed without allocating goes on the matching or a new line."""
        self.build.status = BuildStatus.COMPLETE.value
        self.build.save()

        stock = StockItem.objects.create(part=self.spliced, quantity=600)
        consumed, line = self.build.consume_stock_item(
            stock, Decimal(529), user=self.user, deltas={'maintenance': 1}
        )

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 71)
        self.assertEqual(consumed.quantity, 529)
        self.assertEqual(consumed.consumed_by, self.build)
        self.assertIsNone(consumed.location)
        self.assertEqual(line, self.rope_line)

        self.rope_line.refresh_from_db()
        self.assertEqual(self.rope_line.consumed, 529)

        entry = StockItemTracking.objects.get(
            item=consumed, tracking_type=StockHistoryCode.BUILD_CONSUMED.value
        )
        self.assertEqual(entry.deltas['buildorder'], self.build.pk)
        self.assertEqual(entry.deltas['maintenance'], 1)

        anchor = StockItem.objects.create(part=self.anchor, quantity=1)
        consumed, line = self.build.consume_stock_item(anchor, 1)
        self.assertEqual(consumed.pk, anchor.pk)
        self.assertEqual(
            (line.custom_part, line.quantity, line.consumed), (self.anchor, 1, 1)
        )

        with self.assertRaises(ValidationError):
            self.build.consume_stock_item(stock, 72)
