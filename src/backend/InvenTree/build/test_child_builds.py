"""Tests for child build orders which build stock for their parent.

An open child build order covers the parent build line it builds for, and its
completed outputs are allocated to that line.
"""

from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import BomItem, Part
from stock.models import StockItem

from .models import Build, BuildItem
from .status_codes import BuildStatus


class ChildBuildTest(InvenTreeAPITestCase):
    """Coverage and automatic allocation of child build output."""

    roles = ['build.view', 'build.add', 'build.change', 'part.view']

    @classmethod
    def setUpTestData(cls):
        """Create a product made from a sub-assembly and a tracked sub-assembly."""
        super().setUpTestData()

        cls.product = Part.objects.create(name='Child product', assembly=True)
        cls.sub = Part.objects.create(
            name='Child sub-assembly', assembly=True, component=True
        )
        cls.other = Part.objects.create(
            name='Child other assembly', assembly=True, component=True
        )
        cls.tracked_product = Part.objects.create(
            name='Child tracked product', assembly=True, trackable=True
        )
        cls.tracked_sub = Part.objects.create(
            name='Child tracked sub', assembly=True, component=True, trackable=True
        )

        # Parts are ordered by name, so each new part moves the tree ids of the others
        for part in [
            cls.product,
            cls.sub,
            cls.other,
            cls.tracked_product,
            cls.tracked_sub,
        ]:
            part.refresh_from_db()

        cls.sub_bom = BomItem.objects.create(
            part=cls.product, sub_part=cls.sub, quantity=1
        )
        BomItem.objects.create(part=cls.product, sub_part=cls.other, quantity=1)

        cls.tracked_bom = BomItem.objects.create(
            part=cls.tracked_product, sub_part=cls.tracked_sub, quantity=1
        )

    def setUp(self):
        """Create a parent build for 3 products and a child build of 5 sub-assemblies."""
        super().setUp()

        self.parent = Build.objects.create(
            part=self.product, reference='BO-9970', quantity=3
        )
        self.line = self.parent.build_lines.get(bom_item=self.sub_bom)

        self.child = Build.objects.create(
            part=self.sub, reference='BO-9971', quantity=5, parent=self.parent
        )

    def add_output(self, build, quantity=1, serial=None):
        """Create an incomplete build output."""
        return StockItem.objects.create(
            part=build.part,
            quantity=quantity,
            serial=serial,
            build=build,
            is_building=True,
        )

    def child_builds(self, line):
        """Return the child builds reported by the API for a build line."""
        response = self.get(
            reverse('api-build-line-detail', kwargs={'pk': line.pk}),
            expected_code=200,
        )
        return response.data['child_builds']

    def test_line_lists_open_child_builds(self):
        """The line reports the open child build and what is left to build."""
        builds = self.child_builds(self.line)

        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0]['pk'], self.child.pk)
        self.assertEqual(builds[0]['reference'], 'BO-9971')
        self.assertEqual(builds[0]['quantity'], 5)

        # Other lines are not covered by it
        other_line = self.parent.build_lines.exclude(pk=self.line.pk).get()
        self.assertEqual(self.child_builds(other_line), [])

        # A closed child build covers nothing
        self.child.status = BuildStatus.CANCELLED.value
        self.child.save()

        self.assertEqual(self.child_builds(self.line), [])

    def test_completed_output_allocated_to_parent(self):
        """Completed outputs are allocated to the parent line, up to what it needs."""
        output = self.add_output(self.child, quantity=2)
        self.child.complete_build_output(output, self.user)

        self.assertEqual(self.line.allocated_quantity(), 2)
        self.assertEqual(self.child_builds(self.line)[0]['quantity'], 3)

        # The line needs one more: the rest stays available
        output = self.add_output(self.child, quantity=3)
        self.child.complete_build_output(output, self.user)

        output.refresh_from_db()
        self.assertEqual(self.line.allocated_quantity(), 3)
        self.assertEqual(output.unallocated_quantity(), 2)

    def test_partial_completion_allocates_completed_part(self):
        """Completing part of an output allocates only the completed stock."""
        output = self.add_output(self.child, quantity=5)
        self.child.complete_build_output(output, self.user, quantity=2)

        allocation = BuildItem.objects.get(build_line=self.line)
        self.assertEqual(allocation.quantity, 2)
        self.assertFalse(allocation.stock_item.is_building)
        self.assertNotEqual(allocation.stock_item.pk, output.pk)

    def test_closed_parent_not_allocated(self):
        """Nothing is allocated to a parent build order which is no longer open."""
        self.parent.status = BuildStatus.CANCELLED.value
        self.parent.save()

        output = self.add_output(self.child, quantity=2)
        self.child.complete_build_output(output, self.user)

        self.assertFalse(BuildItem.objects.filter(stock_item=output).exists())

    def test_build_without_parent_not_allocated(self):
        """A build order without a parent leaves its output unallocated."""
        build = Build.objects.create(part=self.sub, reference='BO-9972', quantity=2)
        output = self.add_output(build, quantity=2)
        build.complete_build_output(output, self.user)

        self.assertFalse(BuildItem.objects.filter(stock_item=output).exists())
        self.assertEqual(self.line.allocated_quantity(), 0)

    def test_tracked_output_installed_into_parent_output(self):
        """A tracked output goes into the parent's only incomplete output."""
        parent = Build.objects.create(
            part=self.tracked_product, reference='BO-9973', quantity=1
        )
        line = parent.build_lines.get(bom_item=self.tracked_bom)
        child = Build.objects.create(
            part=self.tracked_sub, reference='BO-9974', quantity=1, parent=parent
        )

        # Without a parent output the tracked stock cannot be allocated yet
        output = self.add_output(child, serial='CT-1')
        child.complete_build_output(output, self.user)
        self.assertFalse(BuildItem.objects.filter(stock_item=output).exists())

        parent_output = self.add_output(parent, serial='CP-1')
        child = Build.objects.create(
            part=self.tracked_sub, reference='BO-9976', quantity=1, parent=parent
        )

        output = self.add_output(child, serial='CT-2')
        child.complete_build_output(output, self.user)

        allocation = BuildItem.objects.get(build_line=line)
        self.assertEqual(allocation.stock_item, output)
        self.assertEqual(allocation.install_into, parent_output)

    def test_part_requirements_building_for_build_orders(self):
        """The part reports what open child build orders build for their parents."""
        Build.objects.create(part=self.sub, reference='BO-9975', quantity=4)

        url = reverse('api-part-requirements', kwargs={'pk': self.sub.pk})

        data = self.get(url, expected_code=200).data
        self.assertEqual(data['scheduled_to_build'], 9)
        self.assertEqual(data['building_for_build_orders'], 5)

        self.parent.status = BuildStatus.CANCELLED.value
        self.parent.save()

        data = self.get(url, expected_code=200).data
        self.assertEqual(data['building_for_build_orders'], 0)
