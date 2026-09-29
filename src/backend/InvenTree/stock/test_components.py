"""Regression tests for servicing finished and sold units."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.urls import path, reverse
from django.utils import timezone

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema
from oauth2_provider.models import AccessToken

from build.models import Build, BuildLine
from build.status_codes import BuildStatus
from common.models import InvenTreeCustomUserStateModel
from company.models import Company
from InvenTree.unit_test import InvenTreeAPITestCase
from order.models import SalesOrder, SalesOrderAllocation, SalesOrderLineItem
from order.status_codes import SalesOrderStatus
from part.models import Part
from stock.components import StockItemComponents
from stock.models import StockItem, StockItemTracking, StockLocation
from stock.status_codes import StockStatus


class ComponentServiceTests(InvenTreeAPITestCase):
    """Use the real API, stock splitting, and stock tracking operations."""

    roles = ['stock.view', 'stock.change']

    @classmethod
    def setUpTestData(cls):
        """Create a completed build with an individually identified output."""
        super().setUpTestData()
        cls.product = Part.objects.create(
            name='Service unit', assembly=True, trackable=True
        )
        cls.part = Part.objects.create(
            name='Service component', component=True, salable=True
        )
        cls.build = Build.objects.create(
            part=cls.product,
            reference='BO-9001',
            quantity=1,
            completed=1,
            status=BuildStatus.COMPLETE,
        )
        cls.unit = StockItem.objects.create(
            part=cls.product, quantity=1, serial='SERVICE-1', build=cls.build
        )
        cls.location = StockLocation.objects.create(name='Service shelf')
        cls.component = StockItem.objects.create(
            part=cls.part, quantity=3, belongs_to=cls.unit, consumed_by=cls.build
        )
        cls.source = StockItem.objects.create(
            part=cls.part, quantity=10, location=cls.location
        )
        cls.url = reverse('api-stock-item-components', kwargs={'pk': cls.unit.pk})

    def action(self, action, expected_code=200, **kwargs):
        """Submit an operation with the requested optional inputs."""
        return self.post(
            self.url, {'action': action, **kwargs}, expected_code=expected_code
        )

    def test_add_after_completion_outside_bom(self):
        """An extra part can be installed without replacing anything or changing the BOM."""
        response = self.action('add', stock_item=self.source.pk, quantity=2)
        installed = StockItem.objects.get(pk=response.data['installed'])
        self.assertEqual(installed.belongs_to_id, self.unit.pk)
        self.assertEqual(installed.quantity, 2)
        self.assertEqual(installed.consumed_by_id, self.build.pk)
        self.source.refresh_from_db()
        self.assertEqual(self.source.quantity, 8)
        self.build.refresh_from_db()
        self.assertEqual(self.build.status, BuildStatus.COMPLETE)
        self.assertEqual(self.build.completed, 1)
        self.assertFalse(self.product.bom_items.exists())
        requirement = BuildLine.objects.get(build=self.build, custom_part=self.part)
        self.assertEqual(requirement.quantity, 2)
        self.assertEqual(requirement.consumed, 2)

    def test_existing_custom_requirement_is_consumed_before_it_is_extended(self):
        """Maintenance uses an existing build-only requirement before increasing it."""
        requirement = BuildLine.objects.create(
            build=self.build, custom_part=self.part, quantity=4
        )
        self.action('add', stock_item=self.source.pk, quantity=2)
        requirement.refresh_from_db()
        self.assertEqual(requirement.quantity, 4)
        self.assertEqual(requirement.consumed, 2)

    def test_destroy_without_removing_or_replacing(self):
        """Only the selected quantity is destroyed and remains installed."""
        response = self.action('destroy', component=self.component.pk, quantity=1)
        destroyed = StockItem.objects.get(pk=response.data['component'])
        self.assertEqual(destroyed.status, StockStatus.DESTROYED)
        self.assertEqual(destroyed.belongs_to_id, self.unit.pk)
        self.assertEqual(destroyed.consumed_by_id, self.build.pk)
        self.assertEqual(destroyed.quantity, 1)
        self.component.refresh_from_db()
        self.assertEqual(self.component.quantity, 2)
        self.assertEqual(self.component.status, StockStatus.OK)
        self.assertFalse(destroyed.in_stock)
        result = self.get(self.url).data
        row = next(item for item in result['components'] if item['pk'] == destroyed.pk)
        self.assertEqual(row['status'], StockStatus.DESTROYED)

    def test_remove_usable_without_replacement(self):
        """Removal can return good components to inventory."""
        response = self.action(
            'remove', component=self.component.pk, quantity=2, location=self.location.pk
        )
        returned = StockItem.objects.get(pk=response.data['component'])
        self.assertIsNone(returned.belongs_to)
        self.assertIsNone(returned.consumed_by)
        self.assertEqual(returned.location_id, self.location.pk)
        self.assertTrue(returned.in_stock)
        self.component.refresh_from_db()
        self.assertEqual(self.component.quantity, 1)

    def test_replace_and_destroy_old_quantity(self):
        """Replacement deducts fresh stock once and excludes the destroyed old stock."""
        response = self.action(
            'replace',
            component=self.component.pk,
            stock_item=self.source.pk,
            quantity=2,
            replacement_quantity=1,
            location=self.location.pk,
            disposition='destroyed',
        )
        old = StockItem.objects.get(pk=response.data['component'])
        new = StockItem.objects.get(pk=response.data['installed'])
        self.assertIsNone(old.belongs_to)
        self.assertEqual(old.status, StockStatus.DESTROYED)
        self.assertEqual(old.quantity, 2)
        self.assertFalse(old.in_stock)
        self.assertEqual(new.belongs_to_id, self.unit.pk)
        self.assertEqual(new.quantity, 1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.quantity, 9)

    def test_replace_can_keep_old_component(self):
        """Replacement does not imply destruction."""
        response = self.action(
            'replace',
            component=self.component.pk,
            stock_item=self.source.pk,
            quantity=3,
            location=self.location.pk,
            disposition='keep',
        )
        old = StockItem.objects.get(pk=response.data['component'])
        self.assertTrue(old.in_stock)
        self.assertEqual(old.status, StockStatus.OK)

    def test_all_actions_after_sale_preserve_orders(self):
        """A sold output can be serviced without returning or reopening it."""
        customer = Company.objects.create(name='Service customer', is_customer=True)
        order = SalesOrder.objects.create(
            customer=customer, reference='SO-9001', status=SalesOrderStatus.COMPLETE
        )
        self.unit.customer = customer
        self.unit.sales_order = order
        self.unit.save()
        added = self.action('add', stock_item=self.source.pk).data['installed']
        self.action('destroy', component=added)
        self.action('remove', component=added, location=self.location.pk)
        self.action(
            'replace',
            component=self.component.pk,
            stock_item=self.source.pk,
            location=self.location.pk,
        )
        self.unit.refresh_from_db()
        self.build.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.unit.customer_id, customer.pk)
        self.assertEqual(self.unit.sales_order_id, order.pk)
        self.assertEqual(self.unit.build_id, self.build.pk)
        self.assertEqual(self.build.status, BuildStatus.COMPLETE)
        self.assertEqual(order.status, SalesOrderStatus.COMPLETE)
        self.assertEqual(StockItem.objects.get(pk=added).status, StockStatus.DESTROYED)

    def test_failed_install_rolls_back_removal_and_splits(self):
        """Even a failure after removal leaves stock and tracking unchanged."""
        stock_count = StockItem.objects.count()
        tracking_count = StockItemTracking.objects.count()
        with patch.object(
            StockItem,
            'installStockItem',
            side_effect=ValidationError('Installation failed'),
        ):
            self.action(
                'replace',
                expected_code=400,
                component=self.component.pk,
                stock_item=self.source.pk,
                quantity=2,
                location=self.location.pk,
                disposition='destroyed',
            )
        self.component.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(self.component.belongs_to_id, self.unit.pk)
        self.assertEqual(self.component.status, StockStatus.OK)
        self.assertEqual(self.component.quantity, 3)
        self.assertEqual(self.source.quantity, 10)
        self.assertEqual(StockItem.objects.count(), stock_count)
        self.assertEqual(StockItemTracking.objects.count(), tracking_count)
        self.assertFalse(
            BuildLine.objects.filter(build=self.build, custom_part=self.part).exists()
        )

    def test_reserved_stock_is_not_available(self):
        """Service must not take inventory reserved for sales."""
        customer = Company.objects.create(name='Other customer', is_customer=True)
        order = SalesOrder.objects.create(customer=customer, reference='SO-9002')
        line = SalesOrderLineItem.objects.create(
            order=order, part=self.part, quantity=9
        )
        SalesOrderAllocation.objects.create(line=line, item=self.source, quantity=9)
        self.action('add', expected_code=400, stock_item=self.source.pk, quantity=2)
        self.action('add', stock_item=self.source.pk, quantity=1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.quantity, 9)

    def test_unrelated_component_rejected(self):
        """A caller cannot remove a component from a different unit."""
        self.action(
            'remove',
            expected_code=400,
            component=self.source.pk,
            location=self.location.pk,
        )

    def test_invalid_source_rejected(self):
        """Destroyed and installed components cannot be picked as replacement stock."""
        self.action('add', expected_code=400, stock_item=self.component.pk)
        self.source.set_status(StockStatus.DESTROYED)
        self.source.save()
        self.action('add', expected_code=400, stock_item=self.source.pk)

    def test_cycles_rejected(self):
        """Reject self-installation and installing an ancestor into its child."""
        self.action('add', expected_code=400, stock_item=self.unit.pk)
        self.unit.belongs_to = self.source
        self.unit.save()
        self.action('add', expected_code=400, stock_item=self.source.pk)

    def test_invalid_quantities(self):
        """Validate quantity before splitting or moving any stock."""
        for quantity in [0, -1, 'NaN', 'Infinity', '0.000001', 11]:
            with self.subTest(quantity=quantity):
                self.action(
                    'add',
                    expected_code=400,
                    stock_item=self.source.pk,
                    quantity=quantity,
                )
        self.action(
            'destroy', expected_code=400, component=self.component.pk, quantity=4
        )

    def test_fractional_quantities(self):
        """Unserialized stock supports quantities in fractional units."""
        response = self.action('add', stock_item=self.source.pk, quantity='0.25')
        installed = StockItem.objects.get(pk=response.data['installed'])
        self.assertEqual(installed.quantity, Decimal('0.25'))
        self.action(
            'remove', component=installed.pk, quantity='0.1', location=self.location.pk
        )
        installed.refresh_from_db()
        self.assertEqual(installed.quantity, Decimal('0.15'))

    def test_missing_fields_and_structural_location(self):
        """Every action requires its actual inputs and a usable destination."""
        for action in ['add', 'remove', 'replace', 'destroy', 'unknown']:
            self.action(action, expected_code=400)
        self.location.structural = True
        self.location.save()
        self.action(
            'remove',
            expected_code=400,
            component=self.component.pk,
            location=self.location.pk,
        )

    def test_requires_single_finished_unit(self):
        """Do not silently change a batch of units or unfinished output."""
        self.unit.is_building = True
        self.unit.save()
        self.action('add', expected_code=400, stock_item=self.source.pk)
        response = self.get(self.url)
        self.assertFalse(response.data['serviceable'])
        self.unit.is_building = False
        self.unit.serial = None
        self.unit.quantity = 2
        self.unit.batch = 'SERVICE-BATCH'
        self.unit.save()
        self.action('add', expected_code=400, stock_item=self.source.pk)

    def test_serialized_source_requires_one(self):
        """Do not split serialized replacement stock into fractional quantities."""
        source = StockItem.objects.create(
            part=self.product, quantity=1, serial='SERVICE-2'
        )
        self.action('add', expected_code=400, stock_item=source.pk, quantity='0.5')
        self.action('add', stock_item=source.pk, quantity=1)
        self.action('destroy', expected_code=400, component=source.pk, quantity='0.5')

    def test_populated_subassembly_cannot_be_partially_split(self):
        """Splitting must not leave nested components attached to the wrong unit."""
        StockItem.objects.create(part=self.part, quantity=1, belongs_to=self.component)
        self.action(
            'remove',
            expected_code=400,
            component=self.component.pk,
            quantity=1,
            location=self.location.pk,
        )
        self.action(
            'remove', component=self.component.pk, quantity=3, location=self.location.pk
        )

    def test_used_status_is_preserved_and_propagated(self):
        """Installing USED stock marks a healthy unit USED; destruction clears the custom key."""
        InvenTreeCustomUserStateModel.objects.create(
            model=ContentType.objects.get_for_model(StockItem),
            reference_status='StockStatus',
            logical_key=10,
            key=15,
            name='USED',
            label='Used',
            color='warning',
        )
        self.source.set_status(15)
        self.source.save()
        installed = self.action('add', stock_item=self.source.pk).data['installed']
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.status_custom_key, 15)
        self.action('destroy', component=installed)
        destroyed = StockItem.objects.get(pk=installed)
        self.assertIsNone(destroyed.status_custom_key)
        self.assertEqual(destroyed.status, StockStatus.DESTROYED)

    def test_view_only_user_cannot_modify(self):
        """Read permission does not authorize stock mutations."""
        self.clearRoles()
        self.assignRole('stock.view')
        result = self.get(self.url)
        self.assertFalse(result.data['can_edit'])
        self.action('add', expected_code=403, stock_item=self.source.pk)

    def test_anonymous_access_rejected(self):
        """Both reads and writes require authentication."""
        self.logout()
        self.get(self.url, expected_code=401)
        self.action('add', expected_code=401, stock_item=self.source.pk)

    def test_oauth_read_scope_cannot_change_components(self):
        """OAuth scopes must authorize the mutation as well as the user's role."""
        self.logout()
        token = AccessToken.objects.create(
            user=self.user,
            token='component-test-read-token',
            expires=timezone.now() + timedelta(hours=1),
            scope='g:read',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.token}')
        self.assertFalse(self.get(self.url).data['can_edit'])
        self.action('add', expected_code=403, stock_item=self.source.pk)
        token.scope = 'r:change:stock'
        token.save()
        self.action('add', stock_item=self.source.pk)

    def test_component_api_schema(self):
        """Publish a valid schema for the new read and action responses."""
        schema = SchemaGenerator(
            patterns=[
                path('api/stock/<int:pk>/components/', StockItemComponents.as_view())
            ]
        ).get_schema(public=True)
        validate_schema(schema)
        operation = schema['paths']['/api/stock/{id}/components/']
        self.assertIn('get', operation)
        self.assertIn('post', operation)
