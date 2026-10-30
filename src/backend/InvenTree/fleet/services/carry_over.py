"""Carry-over: consumed stock which a swap moves from the old device to the new one.

On a swap the old device comes home, but parts consumed by its build order
(typically the whole mooring) stay at sea and go on the new device, which has
its own build order. Consumed stock always sits on the build order of the
device which carries it:

- Before the trip, the manager picks the consumed rows of the old build order
  which the new device takes over (a whole row or part of it). A pending pick
  covers the matching line of the new build order (BuildLine.carried_over), so
  that build order can be completed without allocating those lines.
- The manager then validates the carry-over (even when nothing is picked): a
  replacement goes on a trip only once its carry-over is validated. Changing
  the picks from the deployment clears the validation (changing them from the
  swap task, on the trip, does not).
- Completing the swap task (after the deploy step) moves the picked rows to the
  new build order, with stock history (FLEET_CARRY_OVER). Deploying the
  replacement directly, without a task, moves them at once.
- When the swap does not happen (task or trip cancelled, deployment taken off
  its trip or cancelled), nothing moves: the picks are released, the
  validation is cleared and the lines of the new build order need stock again
  (a completed build order is not reopened; it shows a shortage warning
  instead).

Only consumed rows (belongs_to NULL, not serialized) can be picked: installed
and tracked components are out of scope. The build app knows nothing of the
fleet: it only reads BuildLine.carried_over.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from build.status_codes import BuildStatus
from fleet.helpers import get_model_setting
from fleet.models import (
    CarryOverLine,
    Deployment,
    MaintenanceAction,
    MaintenanceTask,
    TaskType,
)
from fleet.status_codes import DeploymentStatus, TaskStatus, TaskStatusGroups
from InvenTree.helpers import normalize
from stock.models import StockItem
from stock.status_codes import StockHistoryCode

Status = CarryOverLine.Status

# Validation state of the carry-over of a deployment ('' when it has none)
TO_VALIDATE = 'TO_VALIDATE'
VALIDATED = 'VALIDATED'


# ---------------------------------------------------------------------------
# Build orders and rules
# ---------------------------------------------------------------------------


def source_build(deployment: Deployment):
    """The build order of the device which a deployment replaces, if any."""
    replaced = deployment.replaces

    if replaced is None or replaced.device is None:
        return None

    return replaced.device.build


def target_build(deployment: Deployment):
    """The build order of the new device: the deployment's, else its device's."""
    if deployment.build_id:
        return deployment.build

    if deployment.device is not None:
        return deployment.device.build

    return None


def swap_build(task: MaintenanceTask):
    """The build order which takes the stock consumed during a swap task.

    The new device's build order, for a swap task; None for any other task.
    """
    if task.task_type != TaskType.SWAP or task.deployment is None:
        return None

    return target_build(task.deployment)


def open_swap_task(deployment: Deployment) -> MaintenanceTask | None:
    """The open swap task of a deployment, if any."""
    return (
        deployment.tasks.filter(
            task_type=TaskType.SWAP, status__in=TaskStatusGroups.OPEN
        )
        .order_by('pk')
        .first()
    )


def unavailable_reason(deployment: Deployment) -> str:
    """Why this deployment cannot carry anything over ('' when it can)."""
    if (
        deployment.deployment_type != Deployment.DeploymentType.REPLACEMENT
        or deployment.replaces is None
    ):
        return str(_('Only a replacement deployment carries parts over'))

    if source_build(deployment) is None:
        return str(
            _(
                'The replaced device has no build order, so it has no consumed parts to carry over'
            )
        )

    if target_build(deployment) is None:
        return str(_('The new device has no build order to carry the parts over to'))

    return ''


def closed_reason(deployment: Deployment, task: MaintenanceTask | None = None) -> str:
    """Why the picks of this deployment can no longer change ('' while they can)."""
    if task is not None:
        if task.deployment_id != deployment.pk or task.task_type != TaskType.SWAP:
            return str(_('This is not the swap task of the deployment'))

        if task.status not in TaskStatusGroups.OPEN:
            return str(_('The swap task is closed'))

    if (
        deployment.carry_over_lines.filter(status=Status.MOVED).exists()
        or deployment.tasks.filter(
            task_type=TaskType.SWAP, status=TaskStatus.COMPLETED.value
        ).exists()
    ):
        return str(_('The swap is completed: the parts have been carried over'))

    if deployment.status in [
        DeploymentStatus.RECOVERED.value,
        DeploymentStatus.CANCELLED.value,
    ]:
        return str(_('This deployment is closed'))

    if (
        deployment.status == DeploymentStatus.DEPLOYED.value
        and open_swap_task(deployment) is None
    ):
        return str(_('The device is deployed'))

    return ''


def check_editable(deployment: Deployment, task: MaintenanceTask | None = None):
    """Raise unless the picks of the deployment can be changed."""
    if reason := unavailable_reason(deployment) or closed_reason(deployment, task):
        raise ValidationError(reason)


def validation_state(deployment: Deployment) -> str:
    """Whether the carry-over of a deployment is validated.

    Returns:
        '' when the deployment carries nothing over (not a replacement, or a
        build order is missing), else TO_VALIDATE or VALIDATED
    """
    if unavailable_reason(deployment):
        return ''

    return VALIDATED if deployment.carry_over_validated_at else TO_VALIDATE


def check_validated(deployment: Deployment):
    """Raise unless a replacement which carries parts over has been validated."""
    if validation_state(deployment) == TO_VALIDATE:
        raise ValidationError(
            _(
                'Validate the carry-over of {reference} before adding it to a trip'
            ).format(reference=deployment.reference)
        )


@transaction.atomic
def validate(deployment: Deployment, user=None) -> Deployment:
    """Validate the carry-over of a replacement: what is picked is what goes.

    Allowed while the picks can change. Validating with nothing picked means
    that nothing is carried over.
    """
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    check_editable(deployment)

    deployment.carry_over_validated_at = timezone.now()
    deployment.carry_over_validated_by = (
        user if user is not None and user.is_authenticated else None
    )
    deployment.save(
        update_fields=['carry_over_validated_at', 'carry_over_validated_by']
    )

    return deployment


def clear_validation(deployment: Deployment) -> None:
    """Clear the validation of the carry-over (it must be validated again)."""
    if deployment.carry_over_validated_at is None:
        return

    deployment.carry_over_validated_at = None
    deployment.carry_over_validated_by = None
    deployment.save(
        update_fields=['carry_over_validated_at', 'carry_over_validated_by']
    )


def mooring_categories() -> set[int]:
    """The mooring part category (FLEET_MOORING_CATEGORY) and its subcategories."""
    category = get_model_setting('FLEET_MOORING_CATEGORY')

    if category is None:
        return set()

    return set(category.get_descendants(include_self=True).values_list('pk', flat=True))


def pending_elsewhere(item: StockItem, deployment: Deployment) -> Decimal:
    """Quantity of a stock row which other deployments have picked (pending)."""
    return CarryOverLine.objects.filter(stock_item=item, status=Status.PENDING).exclude(
        deployment=deployment
    ).aggregate(total=Sum('quantity'))['total'] or Decimal(0)


def refresh_coverage(lines) -> None:
    """Set BuildLine.carried_over to the pending picks of each line."""
    from build.models import BuildLine

    for line_id in {getattr(line, 'pk', line) for line in lines if line}:
        total = CarryOverLine.objects.filter(
            target_line=line_id, status=Status.PENDING
        ).aggregate(total=Sum('quantity'))['total'] or Decimal(0)

        BuildLine.objects.filter(pk=line_id).update(carried_over=total)


def is_short(line: CarryOverLine) -> bool:
    """Whether a released pick left its completed build order short.

    The line of the new build order lost its cover after the build order was
    completed, and nothing else covers it.
    """
    target = line.target_line

    return (
        line.status == Status.RELEASED
        and target is not None
        and target.build.status == BuildStatus.COMPLETE.value
        and not target.consumable
        and target.required_quantity() > 0
    )


# ---------------------------------------------------------------------------
# Candidates and picks
# ---------------------------------------------------------------------------


def candidates(deployment: Deployment) -> dict:
    """The consumed rows of the old build order which the new device can take over.

    Returns a dict with the two build orders and devices, whether the picks can
    change (and why not), one row per consumed stock row (its quantity, the
    quantity still available (not picked by other deployments), this
    deployment's pending pick, the matching line of the new build order and
    whether it is mooring), and the picks already moved or released.
    """
    old_build = source_build(deployment)
    new_build = target_build(deployment)
    reason = unavailable_reason(deployment)
    closed = closed_reason(deployment)
    task = open_swap_task(deployment)
    replaced = deployment.replaces

    data = {
        'deployment': deployment.pk,
        'source_build': old_build.pk if old_build else None,
        'source_build_reference': old_build.reference if old_build else '',
        'source_device': replaced.device_id if replaced else None,
        'source_device_serial': (
            replaced.device.serial if replaced and replaced.device else ''
        ),
        'target_build': new_build.pk if new_build else None,
        'target_build_reference': new_build.reference if new_build else '',
        'task': task.pk if task else None,
        'task_reference': task.reference if task else '',
        'editable': not (reason or closed),
        'reason': reason or closed,
        'validation': validation_state(deployment),
        'validated_at': deployment.carry_over_validated_at,
        'validated_by': deployment.carry_over_validated_by,
        'lines': [],
        'history': list(
            deployment.carry_over_lines.exclude(status=Status.PENDING)
            .select_related('part', 'target_line__build', 'task')
            .order_by('pk')
        ),
    }

    if reason:
        return data

    picks = {
        line.stock_item_id: line
        for line in deployment.carry_over_lines.filter(status=Status.PENDING)
    }

    target_lines = list(new_build.build_lines.select_related('bom_item', 'custom_part'))

    mooring = mooring_categories()

    items = (
        StockItem.objects.filter(consumed_by=old_build, belongs_to__isnull=True)
        .filter(Q(serial='') | Q(serial__isnull=True))
        .select_related('part')
        .order_by('part__name', 'pk')
    )

    for item in items:
        pick = picks.get(item.pk)
        line = new_build.match_build_line(item.part, target_lines)

        data['lines'].append(
            {
                'stock_item': item.pk,
                'part': item.part,
                'batch': item.batch,
                'quantity': item.quantity,
                'available': max(
                    Decimal(0), item.quantity - pending_elsewhere(item, deployment)
                ),
                'picked': pick.quantity if pick else Decimal(0),
                'target_line': line.pk if line else None,
                'target_part_name': line.part.full_name if line else '',
                'mooring': item.part.category_id in mooring,
            }
        )

    return data


@transaction.atomic
def set_picks(
    deployment: Deployment, lines: list[dict], user=None, task=None
) -> list[CarryOverLine]:
    """Set (replace) the pending picks of a replacement deployment.

    Arguments:
        deployment: The replacement deployment
        lines: [{'stock_item': StockItem, 'quantity': Decimal}]; a stock row
            left out (or with quantity 0) is not carried over
        user: The user picking
        task: The swap task, when picking from the task (refused once closed)

    Allowed until the swap task is completed: refused once the swap is
    completed, the task (if given) is closed, or the deployment is closed.
    A change made without the task clears the validation.

    Returns:
        The pending picks
    """
    Deployment.objects.select_for_update().filter(pk=deployment.pk).first()
    deployment.refresh_from_db()

    if task is not None:
        MaintenanceTask.objects.select_for_update().filter(pk=task.pk).first()
        task.refresh_from_db()

    check_editable(deployment, task)

    old_build = source_build(deployment)
    new_build = target_build(deployment)

    wanted = {}

    for line in lines:
        item = line['stock_item']
        quantity = Decimal(line.get('quantity') or 0)

        if item.pk in wanted:
            raise ValidationError(
                _('Stock item {item} is listed twice').format(item=item)
            )

        if quantity < 0:
            raise ValidationError(_('Quantity must not be negative'))

        if quantity > 0:
            wanted[item.pk] = quantity

    items = {
        item.pk: item
        for item in StockItem.objects.select_for_update()
        .filter(pk__in=wanted.keys())
        .select_related('part')
    }

    for pk, quantity in wanted.items():
        item = items[pk]

        if item.consumed_by_id != old_build.pk:
            raise ValidationError(
                _('{item} is not consumed by build order {build}').format(
                    item=item, build=old_build.reference
                )
            )

        if item.belongs_to_id is not None or item.serialized:
            raise ValidationError(
                _(
                    '{item} is an installed (tracked) component: only consumed parts can be carried over'
                ).format(item=item)
            )

        available = item.quantity - pending_elsewhere(item, deployment)

        if quantity > available:
            message = _('Only {available} of {part} can be carried over').format(
                available=normalize(max(available, 0)), part=item.part.full_name
            )

            if available < item.quantity:
                message += ' ' + str(_('(another deployment has picked the rest)'))

            raise ValidationError(message)

    existing = {
        line.stock_item_id: line
        for line in deployment.carry_over_lines.select_for_update().filter(
            status=Status.PENDING
        )
    }

    changed = {pk: line.quantity for pk, line in existing.items()} != wanted
    touched = {line.target_line_id for line in existing.values()}
    target_lines = list(new_build.build_lines.select_related('bom_item', 'custom_part'))
    now = timezone.now()
    picker = user if user is not None and user.is_authenticated else None
    picks = []

    for pk, quantity in wanted.items():
        item = items[pk]
        target = new_build.match_build_line(item.part, target_lines)
        pick = existing.pop(pk, None)

        if pick is None:
            pick = CarryOverLine(deployment=deployment, stock_item=item)

        if pick.pk is None or pick.quantity != quantity:
            pick.picked_by = picker
            pick.picked_at = now

        pick.part = item.part
        pick.quantity = quantity
        pick.target_line = target
        pick.save()

        touched.add(pick.target_line_id)
        picks.append(pick)

    # Rows which were unticked
    CarryOverLine.objects.filter(
        pk__in=[line.pk for line in existing.values()]
    ).delete()

    refresh_coverage(touched)

    if changed and task is None:
        clear_validation(deployment)

    return picks


# ---------------------------------------------------------------------------
# Moving and releasing
# ---------------------------------------------------------------------------


@transaction.atomic
def move(
    deployment: Deployment, user=None, task: MaintenanceTask | None = None
) -> list[CarryOverLine]:
    """Move the picked rows to the new device's build order (the swap happened).

    Runs when the swap task is completed (after the deploy step), or when the
    replacement is deployed without a task. Each row (or the picked part of
    it) is consumed by the new build order from then on, with a
    FLEET_CARRY_OVER tracking entry. A part without a matching line on the new
    build order gets a build-specific line. Each pick records a CARRY_OVER
    action on the task.
    """
    from fleet.services.maintenance import last_tracking_pk, tag_tracking

    picks = list(
        deployment.carry_over_lines.select_for_update()
        .filter(status=Status.PENDING)
        .select_related('stock_item__part', 'target_line')
        .order_by('pk')
    )

    if not picks:
        return []

    old_build = source_build(deployment)
    new_build = target_build(deployment)
    old_device = deployment.replaces.device
    new_device = deployment.device

    if old_build is None or new_build is None:
        raise ValidationError(unavailable_reason(deployment))

    locked = {
        item.pk: item
        for item in StockItem.objects.select_for_update().filter(
            pk__in=[pick.stock_item_id for pick in picks]
        )
    }

    for pick in picks:
        item = locked.get(pick.stock_item_id)

        if (
            item is None
            or item.consumed_by_id != old_build.pk
            or item.belongs_to_id is not None
            or pick.quantity > item.quantity
        ):
            raise ValidationError(
                _(
                    '{part} can no longer be carried over from {build}: untick it first'
                ).format(
                    part=pick.part.full_name if pick.part else pick,
                    build=old_build.reference,
                )
            )

        pick.stock_item = item

    text = (
        str(_('Carried over from'))
        + f' {old_device.serial} ({old_build.reference}) '
        + str(_('to'))
        + f' {new_device.serial if new_device else deployment.reference} ({new_build.reference})'
    )
    notes = f'[{task.reference}] {text}' if task else f'{deployment.reference}: {text}'

    deltas = {
        'deployment': deployment.pk,
        'from_buildorder': old_build.pk,
        'from_device': old_device.pk,
        'device': new_device.pk if new_device else None,
    }

    if task is not None:
        deltas['maintenance'] = task.pk

    after = last_tracking_pk()
    touched = {pick.target_line_id for pick in picks}

    moved = old_build.move_consumed_stock(
        new_build,
        [(pick.stock_item, pick.quantity, pick.target_line) for pick in picks],
        user=user,
        notes=notes,
        tracking_code=StockHistoryCode.FLEET_CARRY_OVER,
        deltas=deltas,
        create_lines=True,
    )

    now = timezone.now()
    actor = user if user is not None and user.is_authenticated else None

    for pick, (item, quantity, line) in zip(picks, moved, strict=True):
        pick.status = Status.MOVED
        pick.moved_item = item
        pick.target_line = line
        pick.task = task
        pick.closed_at = now
        pick.save()

        if task is not None:
            MaintenanceAction.objects.create(
                task=task,
                action=MaintenanceAction.Action.CARRY_OVER,
                component_in=item,
                part=item.part,
                quantity=quantity,
                note=(
                    f'{old_build.reference} -> {new_build.reference}'
                    + (f' ({old_device.serial})' if old_device.serial else '')
                )[:500],
                metadata={
                    'carry_over': pick.pk,
                    'from_buildorder': old_build.reference,
                    'buildorder': new_build.reference,
                    'from_device': old_device.serial,
                    'source_item': pick.stock_item_id,
                },
                created_by=actor,
            )

    if task is not None:
        tag_tracking(
            task,
            after,
            [pick.stock_item_id for pick in picks] + [m[0].pk for m in moved],
        )

    refresh_coverage(touched | {line.pk for _item, _q, line in moved if line})

    return picks


@transaction.atomic
def release(deployment: Deployment, task: MaintenanceTask | None = None) -> int:
    """Release the pending picks of a deployment: the swap did not happen.

    Nothing moves. The lines of the new build order lose their cover (they
    need stock again); a completed build order is not reopened, it shows a
    shortage warning (see is_short). The validation is cleared: planned
    again, the carry-over is picked and validated again.

    Returns:
        The number of released picks
    """
    picks = list(
        deployment.carry_over_lines.select_for_update().filter(status=Status.PENDING)
    )

    now = timezone.now()

    for pick in picks:
        pick.status = Status.RELEASED
        pick.closed_at = now
        pick.task = task
        pick.save()

    refresh_coverage({pick.target_line_id for pick in picks})

    deployment.refresh_from_db(
        fields=['carry_over_validated_at', 'carry_over_validated_by']
    )
    clear_validation(deployment)

    return len(picks)
