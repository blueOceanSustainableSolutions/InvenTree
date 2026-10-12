"""Helpers shared by the fleet tests (no tests here)."""

from fleet.services import pipeline


def plan_unit(unit, device_type, site=None, target_date=None, **kwargs):
    """Plan a deployment for a unit as the app does it.

    The unit gets its row in the deployment list (the stock sync), and "New
    deployment" sets its site, date and type. Other keyword arguments are set
    on the deployment as they are (e.g. client).
    """
    deployment = pipeline.ensure_unit_deployment(unit, device_type)

    deployment = pipeline.new_deployment(
        deployment,
        site=site,
        target_date=target_date,
        deployment_type=kwargs.pop('deployment_type', None),
        replaces=kwargs.pop('replaces', None),
        coverage=kwargs.pop('coverage', None),
    )

    if kwargs:
        for key, value in kwargs.items():
            setattr(deployment, key, value)

        deployment.save()

    return deployment
