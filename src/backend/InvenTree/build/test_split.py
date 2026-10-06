"""Tests for splitting a completed build output into a new build order."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from build.status_codes import BuildStatus
from InvenTree.unit_test import InvenTreeTestCase
from part.models import BomItem, Part
from stock.models import StockItem
from stock.status_codes import StockHistoryCode

from .models import Build, BuildItem, BuildLine


class BuildSplitTest(InvenTreeTestCase):
    """Move one output of a completed multi-unit build order into its own order."""

    @classmethod
    def setUpTestData(cls):
        """Create a completed build of two units with tracked and untracked stock."""
        super().setUpTestData()

        cls.product = Part.objects.create(
            name='Split product', assembly=True, trackable=True
        )
        cls.tracked = Part.objects.create(
            name='Split tracked', component=True, trackable=True
        )
        cls.screw = Part.objects.create(name='Split screw', component=True)
        cls.glue = Part.objects.create(name='Split glue', component=True)
        cls.removed = Part.objects.create(name='Split removed', component=True)

        cls.tracked_bom = BomItem.objects.create(
            part=cls.product, sub_part=cls.tracked, quantity=1
        )
        cls.screw_bom = BomItem.objects.create(
            part=cls.product, sub_part=cls.screw, quantity=4
        )
        # Present in the BOM, but removed from this particular build order
        cls.removed_bom = BomItem.objects.create(
            part=cls.product, sub_part=cls.removed, quantity=1
        )

    def setUp(self):
        """Create the build order to split."""
        super().setUp()

        self.build = Build.objects.create(
            part=self.product, reference='BO-9950', quantity=2
        )
        self.build.build_lines.filter(bom_item=self.removed_bom).delete()
        BuildLine.objects.create(
            build=self.build, custom_part=self.glue, quantity=2, consumed=2
        )
        BuildLine.objects.filter(build=self.build, bom_item=self.tracked_bom).update(
            consumed=2
        )
        BuildLine.objects.filter(build=self.build, bom_item=self.screw_bom).update(
            consumed=8
        )

        self.outputs = [
            StockItem.objects.create(
                part=self.product, quantity=1, serial=str(n), build=self.build
            )
            for n in (1, 2)
        ]
        self.installed = [
            StockItem.objects.create(
                part=self.tracked,
                quantity=1,
                serial=str(n),
                belongs_to=output,
                consumed_by=self.build,
            )
            for n, output in enumerate(self.outputs, start=10)
        ]
        self.screws = StockItem.objects.create(
            part=self.screw, quantity=8, consumed_by=self.build
        )
        self.glue_stock = StockItem.objects.create(
            part=self.glue, quantity=2, consumed_by=self.build
        )

        Build.objects.filter(pk=self.build.pk).update(
            status=BuildStatus.COMPLETE.value, completed=2
        )
        self.build.refresh_from_db()

    def line(self, build, sub_part):
        """Return the build line for a given part."""
        return next(line for line in build.build_lines.all() if line.part == sub_part)

    def split(self, **kwargs):
        """Split the second output with a typical configuration."""
        lines = {line.pk: line.quantity / 2 for line in self.build.build_lines.all()}
        return self.build.split_build_output(
            self.outputs[1],
            'BO-9951',
            lines=lines,
            consumed={self.screws.pk: Decimal(4), self.glue_stock.pk: Decimal(1)},
            **kwargs,
        )

    def test_split(self):
        """The output, its installed items and the chosen stock move to the new order."""
        new_build = self.split(user=self.user, notes='Fix')

        self.build.refresh_from_db()
        self.assertEqual(self.build.quantity, 1)
        self.assertEqual(self.build.completed, 1)
        self.assertEqual(new_build.quantity, 1)
        self.assertEqual(new_build.completed, 1)
        self.assertEqual(new_build.status, BuildStatus.COMPLETE.value)
        self.assertIn('BO-9950', new_build.notes)

        output = StockItem.objects.get(pk=self.outputs[1].pk)
        self.assertEqual(output.build, new_build)
        self.assertEqual(StockItem.objects.get(pk=self.outputs[0].pk).build, self.build)
        self.assertEqual(
            StockItem.objects.get(pk=self.installed[1].pk).consumed_by, new_build
        )
        self.assertEqual(
            StockItem.objects.get(pk=self.installed[0].pk).consumed_by, self.build
        )
        self.assertTrue(
            output.tracking_info.filter(
                tracking_type=StockHistoryCode.BUILD_ORDER_SPLIT.value
            ).exists()
        )

        # Partially moved stock is split; fully kept stock is untouched
        self.screws.refresh_from_db()
        self.assertEqual(self.screws.quantity, 4)
        self.assertEqual(self.screws.consumed_by, self.build)
        moved_screws = StockItem.objects.get(part=self.screw, consumed_by=new_build)
        self.assertEqual(moved_screws.quantity, 4)
        self.assertEqual(
            StockItem.objects.get(part=self.glue, consumed_by=new_build).quantity, 1
        )

        # The removed BOM line is not recreated; quantities and consumption are split
        self.assertFalse(
            new_build.build_lines.filter(bom_item=self.removed_bom).exists()
        )
        self.assertEqual(new_build.build_lines.count(), 3)

        for sub_part, required, consumed in [
            (self.tracked, 1, 1),
            (self.screw, 4, 4),
            (self.glue, 1, 1),
        ]:
            for build in (self.build, new_build):
                line = self.line(build, sub_part)
                self.assertEqual(line.quantity, required)
                self.assertEqual(line.consumed, consumed)

        self.assertTrue(self.line(new_build, self.glue).is_custom)

    def test_allocations(self):
        """Unassigned allocations can be partly moved to the new order."""
        stock = StockItem.objects.create(part=self.screw, quantity=10)
        screw_line = self.line(self.build, self.screw)
        alloc = BuildItem.objects.create(
            build_line=screw_line, stock_item=stock, quantity=6
        )

        new_build = self.split(allocations={alloc.pk: Decimal(2)})

        alloc.refresh_from_db()
        self.assertEqual(alloc.quantity, 4)
        moved = BuildItem.objects.get(build_line=self.line(new_build, self.screw))
        self.assertEqual(moved.quantity, 2)
        self.assertEqual(moved.stock_item, stock)

    def test_invalid(self):
        """Invalid splits are rejected without changing anything."""
        with self.assertRaises(ValidationError):
            self.build.split_build_output(
                self.outputs[1],
                'BO-9951',
                lines={},
                consumed={self.screws.pk: Decimal(9)},
            )

        # The original reference cannot be reused
        with self.assertRaises(ValidationError):
            self.build.split_build_output(self.outputs[1], 'BO-9950', lines={})

        # The build order must keep at least one output
        Build.objects.filter(pk=self.build.pk).update(quantity=1)
        self.build.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.build.split_build_output(self.outputs[1], 'BO-9951', lines={})

        self.assertFalse(Build.objects.filter(reference='BO-9951').exists())
        self.assertEqual(StockItem.objects.get(pk=self.outputs[1].pk).build, self.build)

        # Only completed build orders can be split
        Build.objects.filter(pk=self.build.pk).update(
            quantity=2, status=BuildStatus.PRODUCTION.value
        )
        self.build.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.split()

    @override_settings(
        SITE_URL='http://testserver', CSRF_TRUSTED_ORIGINS=['http://testserver']
    )
    def test_admin_view(self):
        """The admin view pre-fills the proportional share and performs the split."""
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save()

        url = reverse('admin:build_build_split', args=[self.build.pk])

        response = self.client.get(url)
        self.assertContains(response, f'?output={self.outputs[1].pk}')

        response = self.client.get(url, {'output': self.outputs[1].pk})
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form[f'consumed_{self.screws.pk}'].initial, 4)
        self.assertEqual(form[f'consumed_{self.glue_stock.pk}'].initial, 1)

        data = {
            name: form[name].initial
            for name in form.fields
            if form[name].initial is not None
        }
        data.update({'output': self.outputs[1].pk, 'reference': 'BO-9951'})

        response = self.client.post(url, data)
        new_build = Build.objects.get(reference='BO-9951')
        self.assertRedirects(
            response, reverse('admin:build_build_change', args=[new_build.pk])
        )
        self.assertEqual(self.line(new_build, self.screw).consumed, 4)

        response = self.client.get(
            reverse('admin:build_build_change', args=[self.build.pk])
        )
        self.assertContains(response, url)
