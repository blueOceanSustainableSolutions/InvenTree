"""Maintain the actual components of a finished stock item, including sold units.

These service operations deliberately accept parts outside the product BOM: they
describe the unit's current contents, not a change to its manufacturing recipe.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings

from build.models import BuildLine
from common.models import InvenTreeCustomUserStateModel
from InvenTree.permissions import InvenTreeTokenMatchesOASRequirements, RolePermission
from stock.models import StockItem, StockLocation
from stock.status_codes import StockStatus
from users.authentication import ExtendedOAuth2Authentication
from users.permissions import check_user_role


class InstalledComponentSerializer(serializers.Serializer):
    """Current state of one installed component."""

    pk = serializers.IntegerField()
    part = serializers.CharField()
    part_id = serializers.IntegerField()
    serial = serializers.CharField(allow_null=True)
    batch = serializers.CharField(allow_null=True)
    quantity = serializers.DecimalField(max_digits=15, decimal_places=5)
    serialized = serializers.BooleanField()
    status = serializers.IntegerField()
    status_text = serializers.CharField()
    has_components = serializers.BooleanField()


class ComponentListSerializer(serializers.Serializer):
    """The finished unit and its installed contents."""

    pk = serializers.IntegerField()
    part = serializers.CharField()
    serial = serializers.CharField(allow_null=True)
    quantity = serializers.DecimalField(max_digits=15, decimal_places=5)
    can_edit = serializers.BooleanField()
    serviceable = serializers.BooleanField()
    components = InstalledComponentSerializer(many=True)


class ComponentActionResultSerializer(serializers.Serializer):
    """IDs of the resulting stock items (which may have been split)."""

    item = serializers.IntegerField()
    component = serializers.IntegerField(allow_null=True)
    installed = serializers.IntegerField(allow_null=True)
    action = serializers.CharField()


class ComponentActionSerializer(serializers.Serializer):
    """An independent installation, removal, destruction, or replacement."""

    action = serializers.ChoiceField(choices=['add', 'remove', 'destroy', 'replace'])
    component = serializers.IntegerField(min_value=1, required=False)
    stock_item = serializers.IntegerField(min_value=1, required=False)
    quantity = serializers.DecimalField(
        max_digits=15, decimal_places=5, min_value=Decimal('0.00001'), default=1
    )
    replacement_quantity = serializers.DecimalField(
        max_digits=15, decimal_places=5, min_value=Decimal('0.00001'), required=False
    )
    location = serializers.PrimaryKeyRelatedField(
        queryset=StockLocation.objects.filter(structural=False), required=False
    )
    disposition = serializers.ChoiceField(
        choices=['keep', 'damaged', 'destroyed'], default='keep'
    )
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        """Require the inputs appropriate to the requested operation."""
        action = data['action']
        required = []
        if action in ['remove', 'destroy', 'replace']:
            required.append('component')
        if action in ['add', 'replace']:
            required.append('stock_item')
        if action in ['remove', 'replace']:
            required.append('location')
        errors = {
            field: _('This field is required.')
            for field in required
            if field not in data
        }
        if errors:
            raise serializers.ValidationError(errors)
        return data


def _validate_quantity(item, quantity, field):
    """Reject impossible quantities and splitting populated subassemblies."""
    if quantity > item.quantity or (item.serialized and quantity != 1):
        raise serializers.ValidationError({
            field: _(
                'Quantity exceeds available stock or is invalid for a serialized item.'
            )
        })
    if quantity < item.quantity and item.installed_parts.exists():
        raise serializers.ValidationError({
            field: _('A stock item containing components must be moved in full.')
        })


def _consume_build_requirement(build, part, quantity):
    """Consume an existing requirement, extending this build only when necessary."""
    if build is None:
        raise serializers.ValidationError({
            'item': _('The finished unit must belong to a build order.')
        })

    line = (
        BuildLine.objects
        .select_for_update()
        .filter(build=build)
        .filter(Q(custom_part=part) | Q(bom_item__sub_part=part))
        .order_by('custom_part_id')
        .first()
    )
    if line is None:
        line = BuildLine.objects.create(
            build=build, custom_part=part, quantity=quantity, consumed=0
        )
    shortage = quantity - max(line.quantity - line.consumed, 0)
    if shortage > 0:
        line.quantity += shortage
    line.consumed += quantity
    line.save(update_fields=['quantity', 'consumed'])
    return line


@transaction.atomic
def change_components(unit_id, data, user):
    """Apply a component operation atomically without editing build or sales orders."""
    ids = {unit_id}
    ids.update(data[field] for field in ['component', 'stock_item'] if field in data)
    # Lock in a consistent order, and validate against the freshly locked rows.
    items = {
        item.pk: item
        for item in StockItem.objects
        .select_for_update()
        .filter(pk__in=ids)
        .order_by('pk')
    }
    unit = items.get(unit_id)
    if not unit:
        raise serializers.ValidationError({'item': _('Stock item does not exist.')})
    if unit.is_building or unit.quantity != 1:
        raise serializers.ValidationError({
            'item': _(
                'Select a single finished unit. Split a batch into individual stock items first.'
            )
        })

    action = data['action']
    quantity = data['quantity']
    notes = data['notes']
    component = None
    source = None

    if action in ['remove', 'destroy', 'replace']:
        component = items.get(data['component'])
        if not component or component.belongs_to_id != unit.pk:
            raise serializers.ValidationError({
                'component': _('Select a component currently installed in this unit.')
            })
        _validate_quantity(component, quantity, 'quantity')
        if component.is_building:
            raise serializers.ValidationError({
                'component': _('Component is still in production.')
            })
        if action == 'destroy' and component.status == StockStatus.DESTROYED:
            raise serializers.ValidationError({
                'component': _('Component is already destroyed.')
            })

    if action in ['add', 'replace']:
        source = items.get(data['stock_item'])
        install_quantity = (
            data.get('replacement_quantity', quantity)
            if action == 'replace'
            else quantity
        )
        if not source or not source.in_stock:
            raise serializers.ValidationError({
                'stock_item': _('Select available stock to install.')
            })
        _validate_quantity(
            source,
            install_quantity,
            'replacement_quantity' if action == 'replace' else 'quantity',
        )
        if install_quantity > source.unallocated_quantity():
            raise serializers.ValidationError({
                'stock_item': _('The requested quantity is reserved for another order.')
            })
        # Reject installing the unit itself or an ancestor inside its own descendant.
        ancestor = unit
        seen = set()
        while ancestor:
            if ancestor.pk in seen or ancestor.pk == source.pk:
                raise serializers.ValidationError({
                    'stock_item': _(
                        'Installing this item would create a component cycle.'
                    )
                })
            seen.add(ancestor.pk)
            ancestor = ancestor.belongs_to

    removed_id = None
    installed_id = None
    if component:
        component = component.splitStock(quantity, user=user, notes=notes)
        removed_id = component.pk
        disposition = 'destroyed' if action == 'destroy' else data['disposition']
        if disposition != 'keep':
            component.set_status(
                StockStatus.DESTROYED
                if disposition == 'destroyed'
                else StockStatus.DAMAGED
            )
            component.save(user=user, notes=notes)
        if action in ['remove', 'replace']:
            component.uninstall_into_location(data['location'], user, notes)

    if source:
        installed = source.splitStock(install_quantity, user=user, notes=notes)
        _consume_build_requirement(unit.build, installed.part, install_quantity)
        unit.installStockItem(
            installed, install_quantity, user, notes, build=unit.build
        )
        installed_id = installed.pk
        # Preserve the existing USED convention, without clearing a fault status.
        if source.status_custom_key == 15:
            used = InvenTreeCustomUserStateModel.objects.filter(
                reference_status='StockStatus', key=15
            ).first()
            if (
                used
                and unit.status == used.logical_key
                and unit.status_custom_key is None
            ):
                unit.set_status(used.key)
                unit.save(user=user, notes=notes)

    return {
        'item': unit.pk,
        'component': removed_id,
        'installed': installed_id,
        'action': action,
    }


class ComponentTokenPermission(InvenTreeTokenMatchesOASRequirements):
    """Require OAuth scopes without falling back to the user's session rights."""

    def has_permission(self, request, view):
        """Enforce token scopes for OAuth and authentication for other clients."""
        if self.is_oauth2ed(request):
            return self.check_oauth2_authentication(request, view)
        return bool(request.user and request.user.is_authenticated)


class StockItemComponents(GenericAPIView):
    """Inspect and service components independently of the unit's order status."""

    queryset = StockItem.objects.all()
    serializer_class = ComponentActionSerializer
    # OAuth middleware can also populate the session user. Resolve bearer tokens
    # first so session authentication cannot bypass their restricted scopes.
    authentication_classes = [
        ExtendedOAuth2Authentication,
        *[
            cls
            for cls in api_settings.DEFAULT_AUTHENTICATION_CLASSES
            if cls is not ExtendedOAuth2Authentication
        ],
    ]
    permission_classes = [IsAuthenticated, RolePermission, ComponentTokenPermission]
    role_required = 'stock'
    rolemap = {'POST': 'change', 'HEAD': 'view'}
    required_alternate_scopes = {
        'GET': [['g:read'], ['r:view:stock']],
        'HEAD': [['g:read'], ['r:view:stock']],
        'OPTIONS': [['g:read']],
        'POST': [['r:change:stock']],
    }

    @extend_schema(responses=ComponentListSerializer)
    def get(self, request, pk):
        """List the directly installed components, including destroyed items."""
        unit = get_object_or_404(StockItem.objects.select_related('part'), pk=pk)
        can_edit = check_user_role(request.user, 'stock', 'change')
        if ComponentTokenPermission().is_oauth2ed(request):
            can_edit = can_edit and any(
                request.auth.is_valid(scopes)
                for scopes in self.required_alternate_scopes['POST']
            )
        components = []
        custom_statuses = dict(
            InvenTreeCustomUserStateModel.objects.filter(
                reference_status='StockStatus'
            ).values_list('key', 'label')
        )
        for item in unit.installed_parts.select_related('part').order_by(
            'part__name', 'pk'
        ):
            components.append({
                'pk': item.pk,
                'part': item.part.full_name,
                'part_id': item.part_id,
                'serial': item.serial,
                'batch': item.batch,
                'quantity': str(item.quantity),
                'serialized': item.serialized,
                'status': item.status,
                'status_text': custom_statuses.get(
                    item.status_custom_key, str(item.status_text)
                ),
                'has_components': item.installed_parts.exists(),
            })
        return Response({
            'pk': unit.pk,
            'part': unit.part.full_name,
            'serial': unit.serial,
            'quantity': str(unit.quantity),
            'can_edit': can_edit,
            'serviceable': not unit.is_building and unit.quantity == 1,
            'components': components,
        })

    @extend_schema(
        request=ComponentActionSerializer, responses=ComponentActionResultSerializer
    )
    def post(self, request, pk):
        """Perform one operation; failed replacements roll back both halves."""
        get_object_or_404(StockItem, pk=pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = change_components(pk, serializer.validated_data, request.user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, 'message_dict') else exc.messages
            )
        return Response(result)
