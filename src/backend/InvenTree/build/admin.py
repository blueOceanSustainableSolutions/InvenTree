"""Admin functionality for the BuildOrder app."""

import decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

import InvenTree.helpers
import stock.models
from build.models import Build, BuildItem, BuildLine
from build.validators import generate_next_build_reference


def quantity_field(max_value, initial):
    """Return a form field for a quantity between zero and max_value."""
    return forms.DecimalField(
        min_value=0,
        max_value=max_value,
        max_digits=15,
        decimal_places=5,
        initial=InvenTree.helpers.normalize(initial, rounding=5),
        widget=forms.NumberInput(attrs={'step': 'any', 'style': 'width: 7em'}),
    )


class BuildSplitForm(forms.Form):
    """Quantities moved from one build order into a new one, alongside one output."""

    reference = forms.CharField(max_length=64, label=_('New build order reference'))

    notes = forms.CharField(
        required=False,
        label=_('Notes'),
        widget=forms.Textarea(attrs={'rows': 2, 'cols': 60}),
    )

    def __init__(self, *args, build, output, **kwargs):
        """Add one quantity field per requirement, consumed stock item and allocation."""
        super().__init__(*args, **kwargs)

        share = decimal.Decimal(output.quantity) / decimal.Decimal(build.quantity)

        lines = list(build.build_lines.select_related('bom_item', 'custom_part'))
        self.rows = {
            line.pk: {
                'line': line,
                'field': f'line_{line.pk}',
                'installed': [],
                'consumed': [],
                'allocations': [],
            }
            for line in lines
        }
        self.unmatched = []
        self.automatic_allocations = []

        for line in lines:
            self.fields[f'line_{line.pk}'] = quantity_field(
                line.quantity, line.quantity * share
            )

        installed = output.installed_parts.filter(consumed_by=build).select_related(
            'part'
        )
        for item in installed:
            if line := build.match_build_line(item.part, lines):
                self.rows[line.pk]['installed'].append(item)

        # Suggest moving each line's proportional share of consumed stock,
        # less what is already installed in the output
        remaining = {
            line.pk: line.consumed * share
            - sum(item.quantity for item in self.rows[line.pk]['installed'])
            for line in lines
        }

        consumed_items = stock.models.StockItem.objects.filter(
            consumed_by=build, belongs_to__isnull=True
        ).select_related('part')

        for item in consumed_items:
            line = build.match_build_line(item.part, lines)
            suggested = decimal.Decimal(0)

            if line and remaining[line.pk] > 0:
                suggested = min(item.quantity, remaining[line.pk])
                if item.serialized:
                    suggested = item.quantity if suggested >= 1 else 0
                remaining[line.pk] -= suggested

            name = f'consumed_{item.pk}'
            self.fields[name] = quantity_field(item.quantity, suggested)
            row = {'item': item, 'field': name}

            if line:
                self.rows[line.pk]['consumed'].append(row)
            else:
                self.unmatched.append(row)

        allocations = BuildItem.objects.filter(build_line__build=build).select_related(
            'stock_item', 'install_into'
        )

        for alloc in allocations:
            if alloc.install_into_id == output.pk:
                self.automatic_allocations.append(alloc)
            elif alloc.install_into_id is None and alloc.build_line_id in self.rows:
                name = f'allocation_{alloc.pk}'
                self.fields[name] = quantity_field(alloc.quantity, decimal.Decimal(0))
                self.rows[alloc.build_line_id]['allocations'].append(
                    {'item': alloc, 'field': name}
                )

    def quantities(self, prefix):
        """Return the cleaned {pk: quantity} values for fields with the given prefix."""
        return {
            int(name.removeprefix(prefix)): value or 0
            for name, value in self.cleaned_data.items()
            if name.startswith(prefix)
        }

    def line_rows(self):
        """Return requirement rows, with their bound fields, for the template."""
        for row in self.rows.values():
            yield {
                **row,
                'field': self[row['field']],
                'consumed': [{**c, 'field': self[c['field']]} for c in row['consumed']],
                'allocations': [
                    {**a, 'field': self[a['field']]} for a in row['allocations']
                ],
            }

    def unmatched_rows(self):
        """Return consumed stock which does not match any requirement."""
        return [{**row, 'field': self[row['field']]} for row in self.unmatched]


@admin.register(Build)
class BuildAdmin(admin.ModelAdmin):
    """Class for managing the Build model via the admin interface."""

    exclude = ['reference_int']

    list_display = ('reference', 'title', 'part', 'status', 'batch', 'quantity')

    search_fields = ['reference', 'title', 'part__name', 'part__description']

    autocomplete_fields = [
        'completed_by',
        'destination',
        'parent',
        'part',
        'project_code',
        'responsible',
        'sales_order',
        'take_from',
    ]

    change_form_template = 'admin/build/build/change_form.html'

    actions = ['split_build_order']

    def get_urls(self):
        """Add the split view to the admin URLs."""
        return [
            path(
                '<path:object_id>/split/',
                self.admin_site.admin_view(self.split_view),
                name='build_build_split',
            ),
            *super().get_urls(),
        ]

    @admin.action(description=_('Split an output into a new build order'))
    def split_build_order(self, request, queryset):
        """Redirect to the split view for a single selected build order."""
        if queryset.count() != 1:
            self.message_user(
                request, _('Select exactly one build order to split'), messages.ERROR
            )
            return None

        return redirect('admin:build_build_split', queryset.first().pk)

    def split_view(self, request, object_id):
        """Move one completed output, and its stock, into a new build order."""
        build = self.get_object(request, object_id)

        if build is None:
            raise Http404

        if not self.has_change_permission(request, build):
            raise PermissionDenied

        change_url = reverse('admin:build_build_change', args=[build.pk])
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'build': build,
            'change_url': change_url,
            'title': _('Split build order %s') % build.reference,
        }

        if not build.is_complete:
            self.message_user(
                request, _('Only completed build orders can be split'), messages.ERROR
            )
            return redirect(change_url)

        outputs = build.build_outputs.filter(is_building=False).select_related(
            'location'
        )
        output_id = request.POST.get('output') or request.GET.get('output')
        output = outputs.filter(pk=output_id).first() if output_id else None

        if output is not None:
            try:
                build.validate_split_build_output(output)
            except ValidationError as e:
                self.message_user(request, ' '.join(e.messages), messages.ERROR)
                output = None

        if output is None:
            context['outputs'] = outputs
            return TemplateResponse(
                request, 'admin/build/build/split_build.html', context
            )

        if request.method == 'POST':
            form = BuildSplitForm(request.POST, build=build, output=output)

            if form.is_valid():
                try:
                    new_build = build.split_build_output(
                        output,
                        form.cleaned_data['reference'],
                        lines=form.quantities('line_'),
                        consumed=form.quantities('consumed_'),
                        allocations=form.quantities('allocation_'),
                        user=request.user,
                        notes=form.cleaned_data['notes'],
                    )
                except ValidationError as e:
                    form.add_error(None, e.messages)
                else:
                    self.message_user(
                        request,
                        _(
                            'Output {output} moved from {old} to new build order {new}'
                        ).format(
                            output=output, old=build.reference, new=new_build.reference
                        ),
                        messages.SUCCESS,
                    )
                    return redirect('admin:build_build_change', new_build.pk)
        else:
            form = BuildSplitForm(
                initial={'reference': generate_next_build_reference()},
                build=build,
                output=output,
            )

        context.update({'output': output, 'form': form})

        return TemplateResponse(request, 'admin/build/build/split_build.html', context)


@admin.register(BuildItem)
class BuildItemAdmin(admin.ModelAdmin):
    """Class for managing the BuildItem model via the admin interface."""

    list_display = ('stock_item', 'quantity')

    autocomplete_fields = ['build_line', 'stock_item', 'install_into']


@admin.register(BuildLine)
class BuildLineAdmin(admin.ModelAdmin):
    """Class for managing the BuildLine model via the admin interface."""

    list_display = ('build', 'bom_item', 'quantity')

    search_fields = ['build__title', 'build__reference', 'bom_item__sub_part__name']

    autocomplete_fields = ['bom_item', 'build']
