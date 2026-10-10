"""Background tasks for the fleet app (collected automatically)."""

import structlog

from InvenTree.tasks import ScheduledTask, scheduled_task

logger = structlog.get_logger('inventree')


@scheduled_task(ScheduledTask.MINUTES, 10)
def fleet_sync_pipeline():
    """Keep the deployments in step with the build orders and the stock.

    - Link build outputs to their deployments, and mark them READY when complete
    - Open, close or update the DEPLOYED deployments from the stock customers

    Build outputs are bulk-created, and customers are often set by bulk
    operations or sales order shipping, so a post_save receiver on StockItem
    is not reliable.
    """
    from fleet.services.pipeline import sync_all
    from fleet.services.stock_sync import sync_stock_deployments

    if count := sync_all():
        logger.info('Fleet pipeline: %s deployments updated', count)

    summary = sync_stock_deployments()

    if summary['opened'] or summary['closed'] or summary['updated']:
        logger.info(
            'Fleet stock sync: %s opened, %s closed, %s updated',
            len(summary['opened']),
            len(summary['closed']),
            len(summary['updated']),
        )

    if summary['errors']:
        logger.error('Fleet stock sync: %s errors', len(summary['errors']))


@scheduled_task(ScheduledTask.MINUTES, 1)
def fleet_poll():
    """Poll the data platform (the service skips until the poll interval has passed)."""
    from fleet.services.monitoring import poll_all

    summary = poll_all()

    if summary and summary['polled']:
        logger.info(
            'Fleet poll: %s devices, %s updated%s',
            summary['polled'],
            summary['updated'],
            ' (platform errors)' if summary['failed'] else '',
        )


@scheduled_task(ScheduledTask.DAILY)
def fleet_daily_planning():
    """Daily fleet planning and housekeeping.

    - Propose preventive maintenance tasks (and cancel stale proposals)
    - Raise or clear the PM alerts and the pipeline (readiness) alerts
    - Prune the position track
    """
    from fleet.services.monitoring import prune_positions
    from fleet.services.planning import run_daily_planning

    summary = run_daily_planning()
    logger.info('Fleet planning: %s', summary)

    if deleted := prune_positions():
        logger.info('Fleet: pruned %s old position fixes', deleted)
