"""Raw materials required to build a part, or a build order, from its BOM.

This is the planned counterpart of stock.raw_materials (which follows the stock
actually consumed). Each sub-assembly is replaced by its own BOM until only
parts which are not assemblies remain. Everything is built from scratch: stock
of sub-assemblies on hand is not taken into account.

Each sub-assembly is assumed to get its own build for exactly the quantity
required, so BOM setup quantities are added once per level, and attrition and
rounding multiples are applied as they are for a build order.
"""

from collections import OrderedDict
from decimal import Decimal

from django.utils.translation import gettext_lazy as _

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.generics import GenericAPIView

from build.models import Build
from InvenTree.helpers import DownloadFile, normalize
from part.models import Part
from stock.raw_materials import XLSX_CONTENT_TYPE, save_workbook, units, write_sheet

NOTE_NO_BOM = _('Assembly without BOM')
NOTE_CYCLE = _('Circular BOM')
NOTE_CONSUMABLE = _('Consumable')


def _expandable(part: Part) -> bool:
    """Whether the part is an assembly with a BOM to break down."""
    return part.assembly and part.get_bom_items().exists()


def _label(part: Part, quantity: Decimal) -> str:
    """Short description of an assembly for the 'via' column."""
    return f'{part.full_name} x{normalize(quantity)}'


def _required(bom_item, quantity: Decimal) -> Decimal:
    """Required quantity, including attrition, setup and rounding."""
    # May be a float when rounded to a multiple
    return Decimal(str(bom_item.get_required_quantity(quantity)))


def _bom_items(part: Part):
    return part.get_bom_items().select_related('sub_part').order_by('sub_part__name')


def _walk(rows, part, quantity, reference, consumable, path, seen):
    """Add the rows for a required quantity of a part, breaking down assemblies."""
    if part.pk in seen:
        note = NOTE_CYCLE
    elif _expandable(part):
        for bom_item in _bom_items(part):
            required = _required(bom_item, quantity)
            _walk(
                rows,
                bom_item.sub_part,
                required,
                bom_item.reference,
                bom_item.consumable,
                [*path, _label(part, quantity)],
                seen | {part.pk},
            )
        return
    elif part.assembly:
        note = NOTE_NO_BOM
    else:
        note = NOTE_CONSUMABLE if consumable else None

    rows.append({
        'part': part,
        'quantity': quantity,
        'path': path,
        'reference': reference or '',
        'note': str(note) if note else '',
    })


def explode_part(part: Part, quantity) -> list[dict]:
    """Return the raw material rows to build a quantity of a part from scratch."""
    rows = []
    quantity = Decimal(quantity)

    for bom_item in _bom_items(part):
        _walk(
            rows,
            bom_item.sub_part,
            _required(bom_item, quantity),
            bom_item.reference,
            bom_item.consumable,
            [],
            frozenset({part.pk}),
        )

    return rows


def explode_build(build: Build) -> list[dict]:
    """Return the raw material rows for the required lines of a build order.

    The build's own lines are used (including build-only requirements), and
    sub-assembly lines are broken down through their BOMs.
    """
    rows = []
    lines = build.build_lines.select_related(
        'bom_item', 'bom_item__sub_part', 'custom_part'
    ).order_by('pk')

    for line in lines:
        bom_item = line.bom_item
        _walk(
            rows,
            line.part,
            Decimal(line.quantity),
            bom_item.reference if bom_item else '',
            bom_item.consumable if bom_item else line.custom_consumable,
            [],
            frozenset({build.part_id}),
        )

    return rows


def summarize(rows: list[dict]) -> list[dict]:
    """Total the raw material rows per part."""
    totals = OrderedDict()

    for row in rows:
        part = row['part']
        entry = totals.setdefault(
            part.pk, {'part': part, 'quantity': Decimal(0), 'notes': set()}
        )
        entry['quantity'] += row['quantity']
        if row['note']:
            entry['notes'].add(row['note'])

    return sorted(totals.values(), key=lambda entry: entry['part'].full_name.lower())


def build_workbook(rows: list[dict]) -> bytes:
    """Create the Excel workbook with a summary and a detail sheet."""
    import openpyxl

    workbook = openpyxl.Workbook()

    def supplier(part):
        if supplier_part := part.default_supplier:
            return supplier_part.supplier.name, supplier_part.SKU
        return '', ''

    write_sheet(
        workbook.active,
        _('Summary'),
        [
            _('Part'),
            _('IPN'),
            _('Description'),
            _('Quantity'),
            _('Units'),
            _('Default Supplier'),
            _('SKU'),
            _('Note'),
        ],
        [
            [
                entry['part'].full_name,
                entry['part'].IPN or '',
                entry['part'].description or '',
                float(normalize(entry['quantity'])),
                units(entry['part']),
                *supplier(entry['part']),
                '; '.join(sorted(entry['notes'])),
            ]
            for entry in summarize(rows)
        ],
    )

    write_sheet(
        workbook.create_sheet(),
        _('Detail'),
        [
            _('Part'),
            _('IPN'),
            _('Quantity'),
            _('Units'),
            _('Via'),
            _('Reference'),
            _('Note'),
        ],
        [
            [
                row['part'].full_name,
                row['part'].IPN or '',
                float(normalize(row['quantity'])),
                units(row['part']),
                ' > '.join(row['path']),
                row['reference'],
                row['note'],
            ]
            for row in rows
        ],
    )

    return save_workbook(workbook)


def _download(rows, name):
    filename = f'raw_materials_{name}.xlsx'.replace(' ', '_')
    return DownloadFile(build_workbook(rows), filename, content_type=XLSX_CONTENT_TYPE)


class PartRawMaterialsOptions(serializers.Serializer):
    """Options for the part raw materials export."""

    quantity = serializers.DecimalField(
        max_digits=15, decimal_places=5, min_value=Decimal('0.00001'), default=1
    )


class PartRawMaterials(GenericAPIView):
    """Download the raw materials required to build a part, as Excel."""

    queryset = Part.objects.all()
    # There is no serializer to infer the model from, so name the role
    role_required = 'part'

    @extend_schema(
        parameters=[
            OpenApiParameter(
                'quantity',
                OpenApiTypes.NUMBER,
                description='Number of assemblies to build (default 1)',
            )
        ],
        responses={200: OpenApiTypes.BINARY},
    )
    def get(self, request, *args, **kwargs):
        """Return the raw materials workbook."""
        part = self.get_object()
        options = PartRawMaterialsOptions(data=request.query_params)
        options.is_valid(raise_exception=True)
        quantity = options.validated_data['quantity']
        rows = explode_part(part, quantity)
        return _download(rows, f'{part.name}_x{normalize(quantity)}')


class BuildRawMaterials(GenericAPIView):
    """Download the raw materials required by a build order, as Excel."""

    queryset = Build.objects.all()
    role_required = 'build'

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    def get(self, request, *args, **kwargs):
        """Return the raw materials workbook."""
        build = self.get_object()
        return _download(explode_build(build), build.reference)
