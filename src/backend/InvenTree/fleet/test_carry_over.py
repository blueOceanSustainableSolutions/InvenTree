"""Tests for the carry-over in a swap (FLEET_PLAN.md section 2.14).

On a swap the old device comes home, but the parts consumed by its build
order (typically the mooring) stay at sea and go on the new device. The
manager picks them on the replacement deployment; the new build order counts
them as covered, and completing the swap task moves them to the new build
order. When the swap does not happen the picks are released.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import include, path, reverse
from django.utils import timezone

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from build.api import disassembly_preview
from build.models import Build, BuildItem, BuildLine
from build.status_codes import BuildStatus
from common.settings import set_global_setting
from fleet.api import fleet_api_urls
from fleet.models import (
    CarryOverLine,
    Deployment,
    DeviceLink,
    MaintenanceAction,
    MaintenanceTask,
    TaskType,
)
from fleet.services import carry_over, maintenance, pipeline, trips
from fleet.services import deployment as deployment_service
from fleet.status_codes import DeploymentStatus
from fleet.test_support import plan_unit, sales_order
from fleet.test_trips import TripDataMixin, TripTestBase
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import BomItem, Part, PartCategory
from stock.models import StockItem, StockItemTracking
from stock.raw_materials import explode
from stock.status_codes import StockHistoryCode, StockStatus

Status = CarryOverLine.Status


class CarryOverDataMixin(TripDataMixin):
    """A system whose build orders consume a mooring and some electronics.

    The BOM has a buoy, a rope (template; the build consumes the spliced
    variant, in metres against a BOM quantity of 1), a chain and a surface
    node. The old build order also has a build-specific anchor line.
    """

    @classmethod
    def create_carry_over_data(cls):
        """Create the trip data, the mooring parts and the BOM."""
        cls.create_trip_data()

        cls.mooring = PartCategory.objects.create(name='Mooring')
        cls.lines_category = PartCategory.objects.create(
            name='Lines', parent=cls.mooring
        )
        cls.electronics = PartCategory.objects.create(name='Electronics')

        def part(name, category, **kwargs):
            return Part.objects.create(
                name=name, category=category, component=True, **kwargs
            )

        cls.buoy = part('Buoy', cls.mooring)
        cls.rope = part('Rope', cls.lines_category, is_template=True)
        cls.spliced = part('Rope with Splices', cls.lines_category, variant_of=cls.rope)
        cls.chain = part('Chain', cls.mooring)
        cls.anchor = part('Anchor', cls.mooring)
        cls.node = part('Surface Node', cls.electronics)

        BomItem.objects.create(part=cls.part, sub_part=cls.buoy, quantity=1)
        BomItem.objects.create(
            part=cls.part, sub_part=cls.rope, quantity=1, allow_variants=True
        )
        BomItem.objects.create(part=cls.part, sub_part=cls.chain, quantity=20)
        BomItem.objects.create(part=cls.part, sub_part=cls.node, quantity=1)

    def configure(self):
        """Configure the fleet settings, and the mooring category."""
        super().configure()
        set_global_setting('FLEET_MOORING_CATEGORY', self.mooring.pk)

    # -- the old device ----------------------------------------------------

    def build_device(self, serial, reference):
        """A device whose (completed) build order consumed the mooring and a node.

        Returns:
            The device, and its consumed stock rows by part pk
        """
        build = Build.objects.create(
            part=self.part, reference=reference, title='HT-S', quantity=1
        )
        BuildLine.objects.create(build=build, custom_part=self.anchor, quantity=1)

        device = StockItem.objects.create(
            part=self.part, quantity=1, serial=serial, build=build, location=self.store
        )

        rows = {}

        for part, quantity in [
            (self.buoy, 1),
            (self.spliced, 529),
            (self.chain, 20),
            (self.anchor, 1),
            (self.node, 1),
        ]:
            rows[part.pk] = StockItem.objects.create(
                part=part, quantity=quantity, consumed_by=build
            )
            line = build.match_build_line(part)
            line.consumed += quantity
            line.save()

        # A tracked component installed in the device (out of scope)
        rows['spotter'] = StockItem.objects.create(
            part=self.battery,
            quantity=1,
            serial=f'SPOT-{serial}',
            belongs_to=device,
            consumed_by=build,
        )

        Build.objects.filter(pk=build.pk).update(
            status=BuildStatus.COMPLETE.value, completed=1
        )

        return device, rows

    def deploy_device(self, device, site):
        """Deploy a unit at a site."""
        dep = plan_unit(device, self.device_type, site=site)
        deployment_service.deploy(dep, deployed_at=timezone.now() - timedelta(days=170))
        DeviceLink.objects.filter(stock_item=device).update(platform_id=device.serial)
        dep.refresh_from_db()
        return dep

    def deploy_old(self, serial='HT-S-016', reference='BO-0097'):
        """Deploy the old device (HT-S-016, built by BO-0097)."""
        device, rows = self.build_device(serial, reference)
        return self.deploy_device(device, self.site), rows

    # -- the new device ----------------------------------------------------

    def new_build(self, reference='BO-0109'):
        """Issue a sold build order for the new device."""
        build = Build.objects.create(
            part=self.part,
            reference=reference,
            title='HT-S',
            quantity=1,
            sales_order=sales_order(self.part),
        )
        build.issue_build()
        build.refresh_from_db()
        return build

    def replacement(self, old, build):
        """Plan the deployment of the new build order as a replacement of old."""
        dep = Deployment.objects.get(build=build)
        return pipeline.new_deployment(
            dep,
            site=old.site,
            deployment_type=Deployment.DeploymentType.REPLACEMENT,
            replaces=old,
        )

    def build_output(self, build, serial='HT-S-017'):
        """Allocate the surface node, and complete the output (not the build)."""
        node = StockItem.objects.create(part=self.node, quantity=1, location=self.store)
        BuildItem.objects.create(
            build_line=build.build_lines.get(bom_item__sub_part=self.node),
            stock_item=node,
            quantity=1,
        )

        output = build.create_build_output(1, serials=[serial]).first()
        build.complete_build_output(output, self.user)
        build.refresh_from_db()
        return output

    def complete_build(self, build):
        """Complete a build order (the fleet hook makes its deployment READY)."""
        build.complete_build(self.user)
        build.refresh_from_db()

    def picks(self, rows, *parts, partial=None):
        """Pick lines: the whole row of each part, plus {part: quantity} rows."""
        lines = [
            {'stock_item': rows[part.pk], 'quantity': rows[part.pk].quantity}
            for part in parts
        ]
        lines += [
            {'stock_item': rows[part.pk], 'quantity': Decimal(quantity)}
            for part, quantity in (partial or {}).items()
        ]
        return lines

    def ready_replacement(self, picks=None, reference='BO-0109', serial='HT-S-017'):
        """Old device deployed, new build order with picks, built and READY.

        Returns:
            (old deployment, old rows, new build, new deployment)
        """
        old, rows = self.deploy_old()
        build = self.new_build(reference)
        new = self.replacement(old, build)

        self.build_output(build, serial)

        if picks is None:
            picks = self.picks(rows, self.buoy, self.spliced, self.chain, self.anchor)

        carry_over.set_picks(new, picks(rows) if callable(picks) else picks, self.user)

        self.complete_build(build)
        new.refresh_from_db()
        return old, rows, build, new

    def start_swap(self, new, kit=()):
        """Put the replacement on a trip, take the kit, and start the swap task."""
        trip = self.new_trip(deployments=[new])
        task = MaintenanceTask.objects.get(deployment=new, task_type=TaskType.SWAP)

        trips.prepare_kit(trip, [{'stock_item': new.device}, *kit], user=self.user)
        trips.start_trip(trip, user=self.user)
        maintenance.start(task, user=self.user)
        task.refresh_from_db()
        return trip, task

    def line(self, build, part):
        """The line of a build order which takes a part."""
        return build.match_build_line(part)


class CarryOverTestBase(CarryOverDataMixin, TripTestBase):
    """Service tests for the carry-over."""

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_carry_over_data()


class CarryOverScenarioTest(CarryOverTestBase):
    """The BO-0097 / HT-S-016 -> new device scenario, end to end."""

    def test_full_swap(self):
        """The mooring moves from the old build order to the new one."""
        old, rows = self.deploy_old()
        old_device = old.device
        old_build = old_device.build

        # Manufacturing builds the new device; only the node is allocated
        build = self.new_build()
        new = self.replacement(old, build)
        self.build_output(build)

        self.assertFalse(build.can_complete)
        self.assertEqual(
            set(build.unallocated_lines().values_list('pk', flat=True)),
            {self.line(build, part).pk for part in [self.buoy, self.rope, self.chain]},
        )

        # The manager picks the whole mooring before the trip
        data = carry_over.candidates(new)
        self.assertTrue(data['editable'])
        self.assertEqual(data['source_build'], old_build.pk)
        self.assertEqual(data['target_build'], build.pk)

        by_part = {line['part'].pk: line for line in data['lines']}

        # Consumed rows only: the installed tracked component is not offered
        self.assertEqual(
            set(by_part),
            {
                self.buoy.pk,
                self.spliced.pk,
                self.chain.pk,
                self.anchor.pk,
                self.node.pk,
            },
        )
        self.assertTrue(by_part[self.spliced.pk]['mooring'])  # subcategory
        self.assertFalse(by_part[self.node.pk]['mooring'])

        # The spliced rope (a variant) matches the template rope line
        self.assertEqual(
            by_part[self.spliced.pk]['target_line'], self.line(build, self.rope).pk
        )
        # The anchor has no line on the new build order
        self.assertIsNone(by_part[self.anchor.pk]['target_line'])

        mooring = [line for line in data['lines'] if line['mooring']]
        carry_over.set_picks(
            new,
            [
                {
                    'stock_item': StockItem.objects.get(pk=line['stock_item']),
                    'quantity': line['quantity'],
                }
                for line in mooring
            ],
            self.user,
        )

        # Covered "From HT-S-016": the build order completes without them
        build.refresh_from_db()
        rope_line = self.line(build, self.rope)
        self.assertEqual(rope_line.carried_over, 529)
        self.assertTrue(rope_line.is_fully_allocated())
        self.assertEqual(rope_line.unallocated_quantity(), 0)
        self.assertFalse(build.unallocated_lines().exists())
        self.assertFalse(build.is_overallocated())
        self.assertTrue(build.can_complete)

        self.complete_build(build)
        self.assertEqual(build.status, BuildStatus.COMPLETE.value)

        new.refresh_from_db()
        new_device = new.device
        self.assertEqual(new_device.serial, 'HT-S-017')
        self.assertEqual(new.status, DeploymentStatus.READY.value)

        # Nothing moves before the swap
        self.assertEqual(rows[self.buoy.pk].consumed_by, old_build)

        # The trip: the technician deploys the new device and completes the swap
        _trip, task = self.start_swap(new)
        maintenance.deploy(task, user=self.user)
        self.complete(task, new)

        moved = list(CarryOverLine.objects.filter(deployment=new))
        self.assertEqual(len(moved), 4)
        self.assertTrue(all(line.status == Status.MOVED for line in moved))
        self.assertTrue(all(line.task == task for line in moved))

        for part in [self.buoy, self.spliced, self.chain, self.anchor]:
            row = StockItem.objects.get(pk=rows[part.pk].pk)
            self.assertEqual(row.consumed_by, build)
            self.assertIsNone(row.belongs_to)

            entry = StockItemTracking.objects.get(
                item=row, tracking_type=StockHistoryCode.FLEET_CARRY_OVER.value
            )
            self.assertEqual(entry.deltas['buildorder'], build.pk)
            self.assertEqual(entry.deltas['from_buildorder'], old_build.pk)
            self.assertEqual(entry.deltas['maintenance'], task.pk)
            self.assertEqual(entry.deltas['device'], new_device.pk)
            self.assertEqual(entry.deltas['from_device'], old_device.pk)
            self.assertIn(task.reference, entry.notes)

        # The node and the installed component stay with the old device
        self.assertEqual(
            StockItem.objects.get(pk=rows[self.node.pk].pk).consumed_by, old_build
        )
        self.assertEqual(
            StockItem.objects.get(pk=rows['spotter'].pk).belongs_to, old_device
        )

        # BuildLine.consumed is right on both build orders
        old_build.refresh_from_db()
        for part in [self.buoy, self.rope, self.chain, self.anchor]:
            self.assertEqual(self.line(old_build, part).consumed, 0)
        self.assertEqual(self.line(old_build, self.node).consumed, 1)

        build.refresh_from_db()
        self.assertEqual(self.line(build, self.buoy).consumed, 1)
        self.assertEqual(self.line(build, self.rope).consumed, 529)
        self.assertEqual(self.line(build, self.chain).consumed, 20)
        self.assertEqual(self.line(build, self.node).consumed, 1)
        self.assertFalse(build.build_lines.filter(carried_over__gt=0).exists())

        # A build-specific line for the anchor on the new build order
        anchor = build.build_lines.get(custom_part=self.anchor)
        self.assertEqual((anchor.quantity, anchor.consumed), (1, 1))

        # The service and trip reports, and the Teams summary, list it
        actions = task.actions.filter(action=MaintenanceAction.Action.CARRY_OVER)
        self.assertEqual(actions.count(), 4)
        self.assertIn(
            'Carry Over: 529 x Rope with Splices (BO-0097 -> BO-0109 (HT-S-016))',
            maintenance.action_summary(task),
        )

        # Cost follows the parts: raw materials of each device
        new_rows = {row['item'].pk for row in explode(new_device)}
        old_rows = {row['item'].pk for row in explode(old_device)}
        mooring_rows = {
            rows[part.pk].pk
            for part in [self.buoy, self.spliced, self.chain, self.anchor]
        }
        self.assertTrue(mooring_rows <= new_rows)
        self.assertFalse(mooring_rows & old_rows)
        self.assertIn(rows[self.node.pk].pk, old_rows)

        # The old device comes home on its return order; its disassembly no
        # longer lists the moved rows
        return_line = pipeline.get_return_line(old_device)
        return_line.order.receive_line_item(return_line, self.workshop, self.user)
        StockItem.objects.filter(pk=old_device.pk).update(status=StockStatus.OK.value)
        old_device.refresh_from_db()

        preview = {
            row['stock_item'] for row in disassembly_preview(old_device)['items']
        }
        self.assertEqual(preview, {rows[self.node.pk].pk, rows['spotter'].pk})

        # Closed: the picks can no longer change
        with self.assertRaises(ValidationError):
            carry_over.set_picks(new, [], self.user)

        with self.assertRaises(ValidationError):
            carry_over.set_picks(new, [], self.user, task=task)

        self.assertFalse(carry_over.candidates(new)['editable'])

    def test_partial_variant_and_custom_line(self):
        """Part of a row is split off; a variant and a part without a line."""
        old, rows, build, new = self.ready_replacement(
            picks=lambda rows: self.picks(
                rows, self.anchor, partial={self.spliced: 300}
            )
        )
        old_build = old.device.build

        rope_line = self.line(build, self.rope)
        self.assertEqual(rope_line.carried_over, 300)

        _trip, task = self.start_swap(new)
        maintenance.deploy(task, user=self.user)
        self.complete(task, new)

        # The rope row was split: 300 m go, 229 m stay
        source = StockItem.objects.get(pk=rows[self.spliced.pk].pk)
        self.assertEqual(source.quantity, 229)
        self.assertEqual(source.consumed_by, old_build)

        pick = CarryOverLine.objects.get(deployment=new, part=self.spliced)
        self.assertEqual(pick.moved_item.quantity, 300)
        self.assertEqual(pick.moved_item.consumed_by, build)
        self.assertEqual(pick.target_line, rope_line)

        old_build.refresh_from_db()
        self.assertEqual(self.line(old_build, self.rope).consumed, 229)
        self.assertEqual(self.line(build, self.rope).consumed, 300)

        # The anchor got a build-specific line
        anchor = build.build_lines.get(custom_part=self.anchor)
        self.assertEqual(anchor.consumed, 1)
        self.assertEqual(
            CarryOverLine.objects.get(deployment=new, part=self.anchor).target_line,
            anchor,
        )
        self.assertEqual(self.line(old_build, self.anchor).consumed, 0)

    def test_untick_at_sea_and_consume(self):
        """A worn rope is unticked; a new rope from the kit goes to the new build order."""
        old, rows, build, new = self.ready_replacement()
        old_build = old.device.build

        spare = StockItem.objects.create(
            part=self.spliced, quantity=600, location=self.store
        )
        trip, task = self.start_swap(new, kit=[{'stock_item': spare, 'quantity': 529}])

        # Untick the rope (from the task, while it is in progress)
        carry_over.set_picks(
            new,
            self.picks(rows, self.buoy, self.chain, self.anchor),
            self.user,
            task=task,
        )
        build.refresh_from_db()
        self.assertEqual(self.line(build, self.rope).carried_over, 0)

        kit_rope = StockItem.objects.get(part=self.spliced, location=trip.kit_location)
        record = maintenance.consume(
            task, self.user, kit_rope, Decimal(529), note='Worn rope'
        )

        new_rope = StockItem.objects.get(part=self.spliced, consumed_by=build)
        self.assertEqual(new_rope.quantity, 529)
        self.assertEqual(self.line(build, self.rope).consumed, 529)
        self.assertEqual(record.metadata['buildorder'], build.reference)
        self.assertTrue(
            StockItemTracking.objects.filter(
                item=new_rope,
                tracking_type=StockHistoryCode.BUILD_CONSUMED.value,
                deltas__maintenance=task.pk,
            ).exists()
        )

        maintenance.deploy(task, user=self.user)
        self.complete(task, new)

        # The worn rope stays on the old build order, the rest moved
        self.assertEqual(
            StockItem.objects.get(pk=rows[self.spliced.pk].pk).consumed_by, old_build
        )
        self.assertEqual(
            StockItem.objects.get(pk=rows[self.buoy.pk].pk).consumed_by, build
        )
        self.assertEqual(self.line(build, self.rope).consumed, 529)

        # Consuming on another kind of task still takes the stock
        other = StockItem.objects.create(
            part=self.oring, quantity=5, location=self.store
        )
        dep = Deployment.objects.get(pk=new.pk)
        task2 = maintenance.create_task(dep.device, task_type=TaskType.PREVENTIVE)
        maintenance.start(task2, user=self.user)
        maintenance.consume(task2, self.user, other, Decimal(2))
        other.refresh_from_db()
        self.assertEqual(other.quantity, 3)
        self.assertIsNone(other.consumed_by)

    def test_direct_deploy_moves(self):
        """Deploying the replacement without a task moves the picks at once."""
        _old, rows, build, new = self.ready_replacement()

        new.deploy(user=self.user)

        self.assertFalse(
            CarryOverLine.objects.filter(deployment=new, status=Status.PENDING).exists()
        )
        self.assertEqual(
            StockItem.objects.get(pk=rows[self.buoy.pk].pk).consumed_by, build
        )
        self.assertEqual(self.line(build, self.buoy).consumed, 1)

    def test_complete_without_deploy_releases(self):
        """A swap task completed without deploying: nothing moves."""
        old, rows, _build, new = self.ready_replacement()
        _trip, task = self.start_swap(new)

        task.checklist.update(result='OK')
        maintenance.complete(task, user=self.user, summary='Weather')

        self.assertEqual(
            set(
                CarryOverLine.objects.filter(deployment=new).values_list(
                    'status', flat=True
                )
            ),
            {Status.RELEASED},
        )
        self.assertEqual(
            StockItem.objects.get(pk=rows[self.buoy.pk].pk).consumed_by,
            old.device.build,
        )


class CarryOverReleaseTest(CarryOverTestBase):
    """The swap does not happen: nothing moves, and the picks are released."""

    def assert_released(self, rows, build, new, task=None):
        """Every pick is released; the completed build order is short."""
        lines = list(CarryOverLine.objects.filter(deployment=new))
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(line.status == Status.RELEASED for line in lines))
        self.assertTrue(all(line.closed_at for line in lines))

        old_build = new.replaces.device.build

        for part in [self.buoy, self.spliced, self.chain, self.anchor]:
            self.assertEqual(
                StockItem.objects.get(pk=rows[part.pk].pk).consumed_by, old_build
            )

        build.refresh_from_db()
        self.assertEqual(build.status, BuildStatus.COMPLETE.value)  # not reopened
        self.assertFalse(build.build_lines.filter(carried_over__gt=0).exists())

        rope = CarryOverLine.objects.get(deployment=new, part=self.spliced)
        self.assertTrue(carry_over.is_short(rope))

        # The anchor never had a line on the new build order
        anchor = CarryOverLine.objects.get(deployment=new, part=self.anchor)
        self.assertFalse(carry_over.is_short(anchor))

        if task is not None:
            self.assertTrue(all(line.task == task for line in lines))

    def test_task_cancel(self):
        """Cancelling the swap task releases the picks."""
        _old, rows, build, new = self.ready_replacement()
        _trip, task = self.start_swap(new)

        maintenance.cancel(task, user=self.user, reason='Storm')
        self.assert_released(rows, build, new, task=task)

    def test_trip_cancel(self):
        """Cancelling the trip releases the picks."""
        _old, rows, build, new = self.ready_replacement()
        trip = self.new_trip(deployments=[new])

        trips.cancel_trip(trip, user=self.user)
        self.assert_released(rows, build, new)

        # They can be picked again for the next trip: covered again
        new.refresh_from_db()
        carry_over.set_picks(new, self.picks(rows, self.spliced), self.user)
        self.assertEqual(self.line(build, self.rope).carried_over, 529)
        self.assertFalse(
            carry_over.is_short(
                CarryOverLine.objects.filter(
                    deployment=new, part=self.spliced, status=Status.RELEASED
                ).first()
            )
        )

    def test_unschedule(self):
        """Taking the swap off its trip releases the picks."""
        _old, rows, build, new = self.ready_replacement()
        trip = self.new_trip(deployments=[new])
        task = MaintenanceTask.objects.get(deployment=new, task_type=TaskType.SWAP)

        trips.remove_task(trip, task)
        new.refresh_from_db()
        self.assertEqual(new.status, DeploymentStatus.READY.value)
        self.assert_released(rows, build, new)


class CarryOverValidationTest(CarryOverTestBase):
    """Rules of the picks."""

    def setUp(self):
        """An old deployed device and its planned replacement (not built yet)."""
        super().setUp()
        self.old, self.rows = self.deploy_old()
        self.build = self.new_build()
        self.new = self.replacement(self.old, self.build)

    def pick(self, item, quantity, deployment=None, **kwargs):
        """Set one pick."""
        return carry_over.set_picks(
            deployment or self.new,
            [{'stock_item': item, 'quantity': Decimal(quantity)}],
            self.user,
            **kwargs,
        )

    def test_rows(self):
        """Only consumed rows of the old build order, within their quantity."""
        loose = StockItem.objects.create(
            part=self.buoy, quantity=1, location=self.store
        )

        with self.assertRaisesRegex(
            ValidationError, 'not consumed by build order BO-0097'
        ):
            self.pick(loose, 1)

        with self.assertRaisesRegex(ValidationError, 'installed'):
            self.pick(self.rows['spotter'], 1)

        with self.assertRaisesRegex(ValidationError, 'Only 529'):
            self.pick(self.rows[self.spliced.pk], 530)

        with self.assertRaises(ValidationError):
            self.pick(self.rows[self.spliced.pk], -1)

        with self.assertRaisesRegex(ValidationError, 'twice'):
            carry_over.set_picks(
                self.new,
                [
                    {'stock_item': self.rows[self.buoy.pk], 'quantity': 1},
                    {'stock_item': self.rows[self.buoy.pk], 'quantity': 1},
                ],
                self.user,
            )

        self.assertFalse(CarryOverLine.objects.exists())

        # Replacing the picks: unticked rows are removed, coverage follows
        self.pick(self.rows[self.spliced.pk], 529)
        self.assertEqual(self.line(self.build, self.rope).carried_over, 529)

        self.pick(self.rows[self.chain.pk], 20)
        self.assertEqual(self.line(self.build, self.rope).carried_over, 0)
        self.assertEqual(self.line(self.build, self.chain).carried_over, 20)
        self.assertEqual(CarryOverLine.objects.get().part, self.chain)

        carry_over.set_picks(self.new, [], self.user)
        self.assertFalse(CarryOverLine.objects.exists())
        self.assertEqual(self.line(self.build, self.chain).carried_over, 0)

    def test_two_deployments(self):
        """Two replacements cannot pick more than a row holds."""
        other_build = self.new_build('BO-0110')
        other = self.replacement(self.old, other_build)

        self.pick(self.rows[self.spliced.pk], 500)

        with self.assertRaisesRegex(ValidationError, 'Only 29 .*another deployment'):
            self.pick(self.rows[self.spliced.pk], 30, deployment=other)

        self.pick(self.rows[self.spliced.pk], 29, deployment=other)

        line = next(
            line
            for line in carry_over.candidates(other)['lines']
            if line['stock_item'] == self.rows[self.spliced.pk].pk
        )
        self.assertEqual(line['available'], 29)
        self.assertEqual(line['picked'], 29)

    def test_deployments(self):
        """A replacement of a device with a build order only."""
        # Not a replacement
        unit = plan_unit(self.make_device('HT-S-090'), self.device_type)

        with self.assertRaisesRegex(ValidationError, 'replacement'):
            self.pick(self.rows[self.buoy.pk], 1, deployment=unit)

        # An old device without a build order: nothing to pick
        old_unit = StockItem.objects.create(
            part=self.part, quantity=1, serial='HT-S-001', location=self.store
        )
        site = self.site.__class__.objects.create(name='Sagres #2')
        old = self.deploy_device(old_unit, site)
        build = self.new_build('BO-0111')
        new = self.replacement(old, build)

        data = carry_over.candidates(new)
        self.assertEqual(data['lines'], [])
        self.assertFalse(data['editable'])
        self.assertIn('no build order', data['reason'])

        with self.assertRaisesRegex(ValidationError, 'no build order'):
            self.pick(self.rows[self.buoy.pk], 1, deployment=new)

    def test_task_rules(self):
        """From a task: only its open swap task."""
        self.build_output(self.build)
        self.pick(self.rows[self.buoy.pk], 1)
        self.complete_build(self.build)
        self.new.refresh_from_db()

        _trip, task = self.start_swap(self.new)

        other = maintenance.create_task(self.old.device, task_type=TaskType.CORRECTIVE)

        with self.assertRaisesRegex(ValidationError, 'not the swap task'):
            self.pick(self.rows[self.chain.pk], 20, task=other)

        self.pick(self.rows[self.chain.pk], 20, task=task)

        maintenance.cancel(task, user=self.user)
        task.refresh_from_db()

        with self.assertRaisesRegex(ValidationError, 'closed'):
            self.pick(self.rows[self.chain.pk], 20, task=task)


class CarryOverAPITest(CarryOverDataMixin, InvenTreeAPITestCase):
    """The carry-over endpoints."""

    roles = ['fleet.view', 'fleet.change', 'stock.change', 'build.view', 'build.delete']

    @classmethod
    def setUpTestData(cls):
        """Create the shared data."""
        super().setUpTestData()
        cls.create_carry_over_data()

    def setUp(self):
        """Configure the settings and mocks."""
        super().setUp()
        self.configure()
        self.old, self.rows = self.deploy_old()
        self.build = self.new_build()
        self.new = self.replacement(self.old, self.build)

    def url(self, name, obj):
        """URL of a carry-over endpoint."""
        return reverse(name, kwargs={'pk': obj.pk})

    def test_flow(self):
        """Read the candidates, pick, list, and pick from the task."""
        url = self.url('api-fleet-deployment-carry-over', self.new)

        data = self.get(url).data
        self.assertTrue(data['editable'])
        self.assertEqual(data['source_device_serial'], 'HT-S-016')
        self.assertEqual(data['source_build_reference'], 'BO-0097')
        self.assertEqual(data['target_build_reference'], 'BO-0109')
        self.assertEqual(len(data['lines']), 5)

        mooring = [line for line in data['lines'] if line['mooring']]
        self.assertEqual(len(mooring), 4)

        data = self.post(
            url,
            {
                'lines': [
                    {'stock_item': line['stock_item'], 'quantity': line['quantity']}
                    for line in mooring
                ]
            },
            expected_code=200,
        ).data

        self.assertEqual(
            {line['part'] for line in data['lines'] if line['picked']},
            {self.buoy.pk, self.spliced.pk, self.chain.pk, self.anchor.pk},
        )

        # The build line shows what is carried over
        rope_line = self.line(self.build, self.rope)
        response = self.get(
            reverse('api-build-line-detail', kwargs={'pk': rope_line.pk})
        )
        self.assertEqual(float(response.data['carried_over']), 529)

        response = self.get(
            reverse('api-build-line-list'), {'build': self.build.pk, 'allocated': True}
        )
        self.assertIn(rope_line.pk, [line['pk'] for line in response.data])

        # A covered line cannot be removed from the build order
        self.delete(
            reverse('api-build-line-detail', kwargs={'pk': rope_line.pk}),
            expected_code=400,
        )

        # The list, per build order (the badge on the build order page)
        response = self.get(
            reverse('api-fleet-carry-over-list'), {'build': self.build.pk}
        )
        self.assertEqual(len(response.data), 3)  # the anchor has no line yet
        row = response.data[0]
        self.assertEqual(row['status'], 'PENDING')
        self.assertEqual(row['source_device_serial'], 'HT-S-016')
        self.assertEqual(row['deployment_reference'], self.new.reference)
        self.assertFalse(row['short'])

        response = self.get(
            reverse('api-fleet-carry-over-list'), {'deployment': self.new.pk}
        )
        self.assertEqual(len(response.data), 4)

        # Errors are 400s
        response = self.post(
            url,
            {'lines': [{'stock_item': self.rows['spotter'].pk, 'quantity': 1}]},
            expected_code=400,
        )
        self.assertIn('installed', str(response.data))

        # From the swap task
        self.build_output(self.build)
        self.complete_build(self.build)
        trip = trips.create_trip(
            title='Swap', start_date=timezone.now().date(), deployments=[self.new]
        )
        task = MaintenanceTask.objects.get(deployment=self.new, task_type=TaskType.SWAP)

        task_url = self.url('api-fleet-task-carry-over', task)
        data = self.get(task_url).data
        self.assertEqual(data['task'], task.pk)

        self.post(
            task_url,
            {'lines': [{'stock_item': self.rows[self.buoy.pk].pk, 'quantity': 1}]},
            expected_code=200,
        )
        self.assertEqual(
            CarryOverLine.objects.filter(
                deployment=self.new, status=Status.PENDING
            ).count(),
            1,
        )

        response = self.get(reverse('api-fleet-carry-over-list'), {'task': task.pk})
        self.assertEqual(len(response.data), 1)

        # Cancelled: released, and closed for edits
        trips.cancel_trip(trip, user=self.user)
        task.refresh_from_db()
        maintenance.cancel(task, user=self.user)

        self.post(
            task_url,
            {'lines': [{'stock_item': self.rows[self.buoy.pk].pk, 'quantity': 1}]},
            expected_code=400,
        )

        response = self.get(
            reverse('api-fleet-carry-over-list'),
            {'deployment': self.new.pk, 'status': 'RELEASED'},
        )
        self.assertEqual(len(response.data), 1)
        self.assertTrue(response.data[0]['short'])

    def test_permissions(self):
        """Reading needs fleet view; picking needs fleet change and stock change."""
        url = self.url('api-fleet-deployment-carry-over', self.new)
        body = {'lines': [{'stock_item': self.rows[self.buoy.pk].pk, 'quantity': 1}]}

        self.clearRoles()
        self.get(url, expected_code=403)
        self.get(reverse('api-fleet-carry-over-list'), expected_code=403)

        self.assignRole('fleet.view')
        self.get(url, expected_code=200)
        self.get(reverse('api-fleet-carry-over-list'), expected_code=200)
        self.post(url, body, expected_code=403)

        self.assignRole('fleet.change')
        response = self.post(url, body, expected_code=403)
        self.assertIn('stock.change', str(response.data))

        self.assignRole('stock.change')
        self.post(url, body, expected_code=200)

    def test_options_and_schema(self):
        """The forms load, and the endpoints publish a valid OpenAPI schema."""
        task = maintenance.create_task(self.old.device)

        for url in [
            self.url('api-fleet-deployment-carry-over', self.new),
            self.url('api-fleet-task-carry-over', task),
            reverse('api-fleet-carry-over-list'),
        ]:
            self.options(url, expected_code=200)

        schema = SchemaGenerator(
            patterns=[path('api/fleet/', include(fleet_api_urls))]
        ).get_schema(public=True)

        validate_schema(schema)

        for url in [
            '/api/fleet/deployment/{id}/carry-over/',
            '/api/fleet/task/{id}/carry-over/',
        ]:
            self.assertIn('get', schema['paths'][url])
            self.assertIn('post', schema['paths'][url])

        self.assertIn('get', schema['paths']['/api/fleet/carry-over/'])

    def test_no_missing_migrations(self):
        """The models match the migrations (makemigrations --check)."""
        call_command(
            'makemigrations',
            'build',
            'fleet',
            '--check',
            '--dry-run',
            stdout=StringIO(),
        )
