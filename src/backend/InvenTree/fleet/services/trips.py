"""Field trips: planning, parts kit, execution and reconciliation.

A trip groups maintenance tasks (picked freely by the manager, there are no
regions). It owns a non-structural stock location TRIP-xxxx (under
FLEET_KIT_PARENT_LOCATION) which holds the parts kit, and a "Removed" child
location which receives the components removed during the trip.

Lifecycle: PLANNING -> KIT_READY (prepare kit) -> IN_PROGRESS (start) ->
RECONCILING -> CLOSED (reconcile), or CANCELLED. Stock only moves through
StockItem.move() (tracking STOCK_MOVE, like /api/stock/transfer/).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

import structlog

from fleet import notify
from fleet.events import trigger_status_event
from fleet.helpers import get_model_setting
from fleet.models import Deployment, FieldTrip, MaintenanceTask, TripKitLine
from fleet.services import deployment as deployment_service
from fleet.services import maintenance
from fleet.status_codes import TaskStatus, TaskStatusGroups, TripStatus
from InvenTree.helpers import current_date
from stock.models import StockItem, StockLocation
from users.models import Owner

logger = structlog.get_logger('inventree')


# ---------------------------------------------------------------------------
# Kit locations
# ---------------------------------------------------------------------------


def lock_trip(trip: FieldTrip) -> FieldTrip:
    """Lock the trip row, and refresh the caller's instance."""
    FieldTrip.objects.select_for_update().filter(pk=trip.pk).first()
    trip.refresh_from_db()
    return trip


def set_status(trip: FieldTrip, value: int) -> None:
    """Set the status of a trip, and trigger its status event."""
    trip.status = value
    trip.save()
    trigger_status_event(trip)


def ensure_kit_location(trip: FieldTrip) -> StockLocation:
    """Return the kit location of a trip, creating it (and "Removed") if missing.

    The kit location TRIP-xxxx is created under FLEET_KIT_PARENT_LOCATION
    (at the top level when the setting is empty). Both are non-structural.
    """
    location = trip.kit_location

    if location is None:
        parent = get_model_setting('FLEET_KIT_PARENT_LOCATION')

        location = StockLocation.objects.create(
            name=trip.reference,
            parent=parent,
            structural=False,
            description=(str(_('Parts kit of')) + f' {trip}')[:250],
        )

        trip.kit_location = location
        trip.save()

    get_removed_location(trip)

    return location


def get_removed_location(trip: FieldTrip) -> StockLocation | None:
    """Return the "Removed" child of the trip kit location (created if missing)."""
    if trip.kit_location is None:
        return None

    location = trip.kit_location.children.filter(
        name=maintenance.REMOVED_LOCATION_NAME
    ).first()

    if location is None:
        location = StockLocation.objects.create(
            name=maintenance.REMOVED_LOCATION_NAME,
            parent=trip.kit_location,
            structural=False,
            description=str(_('Components removed during')) + f' {trip.reference}',
        )

    return location


def kit_stock(trip: FieldTrip):
    """Stock items in the trip kit (not in "Removed"), which are in stock."""
    if trip.kit_location is None:
        return StockItem.objects.none()

    removed = get_removed_location(trip)

    return (
        StockItem.objects
        .filter(location__in=trip.kit_location.get_descendants(include_self=True))
        .exclude(location=removed)
        .filter(StockItem.IN_STOCK_FILTER)
    )


def removed_stock(trip: FieldTrip):
    """Stock items in the trip "Removed" location (any status)."""
    removed = get_removed_location(trip)

    if removed is None:
        return StockItem.objects.none()

    return StockItem.objects.filter(
        location__in=removed.get_descendants(include_self=True),
        quantity__gt=0,
        belongs_to=None,
        customer=None,
        consumed_by=None,
    )


def kit_locations():
    """Every trip kit location (and their children)."""
    ids = set()

    parent = get_model_setting('FLEET_KIT_PARENT_LOCATION')

    if parent is not None:
        ids.update(
            parent.get_descendants(include_self=True).values_list('pk', flat=True)
        )

    for location in StockLocation.objects.filter(fleet_trips__isnull=False):
        ids.update(
            location.get_descendants(include_self=True).values_list('pk', flat=True)
        )

    return ids


# ---------------------------------------------------------------------------
# Creating trips and adding tasks
# ---------------------------------------------------------------------------


@transaction.atomic
def create_trip(
    title: str,
    start_date,
    end_date=None,
    vessel: str = '',
    responsible=None,
    team=None,
    tasks=None,
    deployments=None,
    **kwargs,
) -> FieldTrip:
    """Create a field trip with its kit location and "Removed" child.

    Tasks and (ready) deployments can be added at once (plan selection).
    """
    if end_date is not None and end_date < start_date:
        raise ValidationError({'end_date': _('The end date is before the start date')})

    trip = FieldTrip.objects.create(
        title=title,
        start_date=start_date,
        end_date=end_date,
        vessel=vessel,
        responsible=responsible,
        **kwargs,
    )

    if team:
        trip.team.set(team)

    ensure_kit_location(trip)

    if tasks or deployments:
        add_tasks(trip, tasks=tasks or [], deployments=deployments or [])

    return trip


@transaction.atomic
def add_tasks(trip: FieldTrip, tasks=None, deployments=None) -> list[MaintenanceTask]:
    """Add tasks, and ready deployments (as DEPLOYMENT / SWAP tasks), to a trip.

    Returns:
        The tasks which are now on the trip
    """
    lock_trip(trip)

    if trip.status not in maintenance.TRIP_PLANNABLE_STATUSES:
        raise ValidationError(_('Tasks can only be added to an open trip'))

    if not tasks and not deployments:
        raise ValidationError(_('Select tasks or deployments to add'))

    scheduled = maintenance.schedule_tasks(trip, list(tasks or []))

    for deployment in deployments or []:
        scheduled.append(deployment_service.schedule(deployment, trip=trip))

    return scheduled


@transaction.atomic
def remove_task(trip: FieldTrip, task: MaintenanceTask) -> MaintenanceTask:
    """Take a task which has not started off a trip (it is PROPOSED again)."""
    lock_trip(trip)

    if task.trip_id != trip.pk:
        raise ValidationError({'task': _('This task is not on the trip')})

    return maintenance.unschedule_task(task)


# ---------------------------------------------------------------------------
# Parts kit
# ---------------------------------------------------------------------------


def stock_quantity(queryset) -> Decimal:
    """Total quantity of a stock queryset."""
    return sum((item.quantity for item in queryset), Decimal(0))


def kit_lines(trip: FieldTrip) -> list[dict]:
    """The kit lines of a trip, with availability.

    For each line:
    - available: stock of the part which is in stock outside every trip kit
    - in_kit: stock of the part in this trip's kit (for a device line: 1 when
      the device is in the kit)
    - for a device line: where the device is, and whether it is in the kit
    """
    excluded = kit_locations()
    in_kit = kit_stock(trip)
    rows = []

    for line in trip.kit_lines.select_related(
        'part', 'stock_item', 'stock_item__location', 'task'
    ).order_by('source', 'part__name', 'pk'):
        row = {'line': line}

        if line.stock_item is not None:
            device = line.stock_item
            taken = in_kit.filter(pk=device.pk).exists()
            row['available'] = Decimal(1) if device.in_stock else Decimal(0)
            row['in_kit'] = Decimal(1) if taken else Decimal(0)
        else:
            stock = StockItem.objects.filter(StockItem.IN_STOCK_FILTER).filter(
                part=line.part
            )
            row['available'] = stock_quantity(stock.exclude(location__in=excluded))
            row['in_kit'] = stock_quantity(in_kit.filter(part=line.part))

        row['missing'] = max(Decimal(line.quantity_planned) - row['in_kit'], Decimal(0))
        rows.append(row)

    return rows


def add_line(lines: dict, part, quantity, source, note='', stock_item=None, task=None):
    """Add a suggested kit line (quantities of the same part and source add up)."""
    key = (part.pk, source, stock_item.pk if stock_item else None)

    if key in lines:
        lines[key]['quantity_planned'] += Decimal(quantity)

        if note and note not in lines[key]['notes']:
            lines[key]['notes'].append(note)
    else:
        lines[key] = {
            'part': part,
            'quantity_planned': Decimal(quantity),
            'source': source,
            'notes': [note] if note else [],
            'stock_item': stock_item,
            'task': task,
        }


@transaction.atomic
def apply_kit_suggestion(trip: FieldTrip, suggestion: list[dict]) -> list[TripKitLine]:
    """Replace the non-MANUAL kit lines of a trip with a suggestion."""
    lock_trip(trip)

    trip.kit_lines.exclude(source=TripKitLine.Source.MANUAL).delete()

    created = []

    for line in suggestion:
        created.append(
            TripKitLine.objects.create(
                trip=trip,
                part=line['part'],
                quantity_planned=line['quantity_planned'],
                source=line['source'],
                note=', '.join(line['notes'])[:250],
                stock_item=line['stock_item'],
                task=line['task'],
            )
        )

    return created


# ---------------------------------------------------------------------------
# Kit preparation, start, reconcile
# ---------------------------------------------------------------------------


@transaction.atomic
def prepare_kit(trip: FieldTrip, items: list[dict], user=None) -> list[StockItem]:
    """Move stock into the trip kit location.

    Arguments:
        trip: The trip (planning, kit ready or in progress)
        items: [{'stock_item': StockItem, 'quantity': Decimal (optional)}]
        user: Who prepares the kit

    A trip in PLANNING becomes KIT_READY, and "TRIP-xxxx ready" is notified.

    Returns:
        The stock items which were moved
    """
    lock_trip(trip)

    if not trip.can_prepare_kit:
        raise ValidationError(_('The kit of a closed trip cannot be changed'))

    if not items:
        raise ValidationError({'items': _('Select the stock to take')})

    location = ensure_kit_location(trip)
    kit_ids = set(
        location.get_descendants(include_self=True).values_list('pk', flat=True)
    )
    notes = str(_('Field trip kit')) + f' {trip.reference}'
    moved = []

    for entry in items:
        item = entry['stock_item']

        StockItem.objects.select_for_update().filter(pk=item.pk).first()
        item.refresh_from_db()

        quantity = entry.get('quantity') or item.quantity

        if item.location_id in kit_ids:
            raise ValidationError({
                'items': _('This stock is already in the kit') + f' ({item})'
            })

        if not item.in_stock or item.is_building:
            raise ValidationError({
                'items': _('This stock item is not in stock') + f' ({item})'
            })

        if quantity <= 0 or quantity > item.quantity:
            raise ValidationError({
                'items': _('Quantity exceeds the available stock') + f' ({item})'
            })

        if item.serialized and quantity != item.quantity:
            raise ValidationError({
                'items': _('Take serialized items whole') + f' ({item})'
            })

        if quantity < item.quantity:
            # A split (as StockItem.move() does): the new item goes into the kit
            moved.append(
                item.splitStock(
                    quantity, location, user, allow_production=True, notes=notes
                )
            )
            continue

        if not item.move(location, notes, user, quantity=quantity):
            raise ValidationError({
                'items': _('The stock could not be moved') + f' ({item})'
            })

        moved.append(item)

    if trip.status == TripStatus.PLANNING.value:
        set_status(trip, TripStatus.KIT_READY.value)
        notify_kit_ready(trip)

    return moved


def notify_kit_ready(trip: FieldTrip) -> None:
    """Notify that the kit of a trip is ready."""
    tasks = trip.tasks.exclude(status=TaskStatus.CANCELLED.value)

    notify.notify(
        'trip_kit_ready',
        f'{trip.reference} ' + str(_('ready')) + f' - {trip.title}',
        str(_('The parts kit has been prepared')),
        link=notify.build_link(f'trips/{trip.pk}', obj=trip),
        severity='INFO',
        obj=trip,
        facts=[
            (_('Trip'), trip.reference),
            (_('Start Date'), trip.start_date),
            (_('Vessel'), trip.vessel or None),
            (_('Tasks'), tasks.count()),
            (_('Kit Items'), kit_stock(trip).count()),
        ],
    )


@transaction.atomic
def start_trip(trip: FieldTrip, user=None) -> FieldTrip:
    """Start a trip (the team is in the field)."""
    lock_trip(trip)

    if not trip.can_start:
        raise ValidationError(_('Only a planned trip can be started'))

    ensure_kit_location(trip)
    set_status(trip, TripStatus.IN_PROGRESS.value)

    if user is not None and user.is_authenticated and trip.responsible is None:
        trip.responsible = Owner.get_owner(user)
        trip.save()

    return trip


def return_location(item: StockItem, returns: dict) -> StockLocation | None:
    """Where a leftover kit item goes: the given location, else the part default."""
    location = returns.get(item.pk)

    if location is None:
        location = item.part.get_default_location()

    return location


def release_tasks(trip: FieldTrip) -> int:
    """Take the tasks which were not started off the trip (PROPOSED again)."""
    count = 0

    for task in trip.tasks.filter(
        status__in=[TaskStatus.PROPOSED.value, TaskStatus.SCHEDULED.value]
    ):
        maintenance.unschedule_task(task)
        count += 1

    return count


@transaction.atomic
def reconcile(trip: FieldTrip, user=None, returns=None) -> dict:
    """Return the kit after a trip, and close it.

    - Leftover kit stock goes to the given location (returns maps a stock item
      pk to a location), else the part's default location.
    - Components in "Removed" go to FLEET_WORKSHOP_LOCATION (unless another
      location is given), keeping their status (e.g. DAMAGED).
    - Tasks which were not started go back to PROPOSED, without the trip.
    - The trip is CLOSED once the kit is empty and every task is completed or
      cancelled; otherwise it stays RECONCILING, and the result says why.

    Returns:
        {'returned': n, 'to_workshop': n, 'released': n, 'missing_location':
        [items], 'open_tasks': [references], 'closed': bool}
    """
    lock_trip(trip)

    if not trip.can_reconcile:
        raise ValidationError(_('Only a trip which is under way can be reconciled'))

    in_progress = list(
        trip.tasks.filter(status=TaskStatus.IN_PROGRESS.value).values_list(
            'reference', flat=True
        )
    )

    if in_progress:
        raise ValidationError(
            _('Complete or cancel the tasks in progress first')
            + f' ({", ".join(in_progress)})'
        )

    returns = {entry['stock_item'].pk: entry['location'] for entry in (returns or [])}

    for location in returns.values():
        if location is not None and location.structural:
            raise ValidationError({
                'returns': _('Stock items cannot be located in structural locations')
            })

    set_status(trip, TripStatus.RECONCILING.value)

    kit_ids = (
        set(
            trip.kit_location.get_descendants(include_self=True).values_list(
                'pk', flat=True
            )
        )
        if trip.kit_location
        else set()
    )

    notes = str(_('Returned from field trip')) + f' {trip.reference}'
    missing = []
    returned = 0

    for item in kit_stock(trip):
        location = return_location(item, returns)

        if location is None or location.pk in kit_ids or location.structural:
            missing.append(item)
            continue

        item.move(location, notes, user)
        returned += 1

    workshop = get_model_setting('FLEET_WORKSHOP_LOCATION')
    to_workshop = 0

    for item in removed_stock(trip):
        location = returns.get(item.pk) or workshop

        if location is None or location.pk in kit_ids or location.structural:
            missing.append(item)
            continue

        move_removed(item, location, notes, user)
        to_workshop += 1

    released = release_tasks(trip)

    open_tasks = list(
        trip.tasks.filter(status__in=TaskStatusGroups.OPEN).values_list(
            'reference', flat=True
        )
    )

    closed = not missing and not open_tasks

    if closed:
        if trip.end_date is None:
            trip.end_date = max(current_date(), trip.start_date)

        set_status(trip, TripStatus.CLOSED.value)

    return {
        'returned': returned,
        'to_workshop': to_workshop,
        'released': released,
        'missing_location': [str(item) for item in missing],
        'open_tasks': open_tasks,
        'closed': closed,
    }


def move_removed(item: StockItem, location: StockLocation, notes: str, user) -> None:
    """Move a removed component on (to the workshop), keeping its status.

    StockItem.move() refuses items which are not "in stock", and a DAMAGED or
    DESTROYED component is not; so the location is changed directly, with
    the same STOCK_MOVE tracking entry.
    """
    from stock.status_codes import StockHistoryCode

    if item.is_in_stock(check_status=False, check_in_production=False):
        if item.move(location, notes, user):
            return

    item.location = location
    item.save(add_note=False)
    item.add_tracking_entry(
        StockHistoryCode.STOCK_MOVE,
        user,
        notes=notes,
        deltas={'location': location.pk, 'quantity': float(item.quantity)},
    )


@transaction.atomic
def cancel_trip(trip: FieldTrip, user=None, reason: str = '') -> FieldTrip:
    """Cancel a trip which has not started.

    Its tasks go back to PROPOSED (deployments to READY). The kit must be
    empty: return it with reconcile first.
    """
    lock_trip(trip)

    if not trip.can_cancel:
        raise ValidationError(_('Only a trip which has not started can be cancelled'))

    if kit_stock(trip).exists() or removed_stock(trip).exists():
        raise ValidationError(
            _('The kit still holds stock: return it to stock first (reconcile)')
        )

    release_tasks(trip)

    if reason:
        trip.notes = (
            (trip.notes + '\n\n' if trip.notes else '')
            + str(_('Cancelled'))
            + f': {reason}'
        )

    set_status(trip, TripStatus.CANCELLED.value)

    return trip


def delete_kit_locations(trip: FieldTrip) -> None:
    """Delete the (empty) kit locations of a trip which is being deleted."""
    location = trip.kit_location

    if location is None:
        return

    tree = location.get_descendants(include_self=True)

    if StockItem.objects.filter(location__in=tree).exists():
        raise ValidationError(_('The kit location of this trip still holds stock'))

    trip.kit_location = None
    trip.save()

    location.delete(delete_sub_locations=True)


def deployments_for(trip: FieldTrip):
    """The deployments scheduled on a trip (through their deployment tasks)."""
    return Deployment.objects.filter(
        tasks__trip=trip, tasks__task_type__in=maintenance.DEPLOY_TASK_TYPES
    ).distinct()
