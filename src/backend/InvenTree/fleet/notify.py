"""Fleet notifications: Microsoft Teams, email and InvenTree in-app.

notify() never raises: a failing channel is logged, and the others still run.
"""

from urllib.parse import urljoin

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.transaction import on_commit
from django.db.models import Q
from django.utils.html import escape

import requests
import structlog

from common.settings import get_global_setting

logger = structlog.get_logger('inventree')

# Request timeout for the Teams webhook (seconds)
TEAMS_TIMEOUT = 10

# Adaptive card colour for each severity
SEVERITY_COLORS = {'CRITICAL': 'Attention', 'WARNING': 'Warning', 'INFO': 'Accent'}

# Slug of the built-in (mandatory) InvenTree UI notification method
UI_NOTIFICATION_SLUG = 'inventree-ui-notification'


def build_link(portal_path: str = '', obj=None) -> str:
    """Return an absolute link for a notification.

    FLEET_PORTAL_URL + portal_path when the portal URL is set, otherwise the
    object's page in the main UI.
    """
    portal_url = get_global_setting('FLEET_PORTAL_URL', cache=False)

    if portal_url:
        return urljoin(portal_url.rstrip('/') + '/', portal_path.lstrip('/'))

    if obj is not None and hasattr(obj, 'get_absolute_url'):
        try:
            from InvenTree.helpers_model import construct_absolute_url

            return construct_absolute_url(obj.get_absolute_url())
        except Exception:
            logger.exception('Fleet: could not build a notification link')

    return ''


def build_teams_card(
    title: str, text: str, link: str = '', severity: str = 'INFO', facts=None
) -> dict:
    """Return a Teams Workflows message carrying an Adaptive Card."""
    body = [
        {
            'type': 'TextBlock',
            'text': title,
            'weight': 'Bolder',
            'size': 'Medium',
            'wrap': True,
            'color': SEVERITY_COLORS.get(severity, 'Default'),
        }
    ]

    if text:
        body.append({'type': 'TextBlock', 'text': text, 'wrap': True})

    if facts:
        body.append({
            'type': 'FactSet',
            'facts': [
                {'title': str(name), 'value': str(value)}
                for name, value in facts
                if value not in (None, '')
            ],
        })

    content = {
        '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
        'type': 'AdaptiveCard',
        'version': '1.4',
        'body': body,
    }

    if link:
        content['actions'] = [{'type': 'Action.OpenUrl', 'title': 'Open', 'url': link}]

    return {
        'type': 'message',
        'attachments': [
            {
                'contentType': 'application/vnd.microsoft.card.adaptive',
                'contentUrl': None,
                'content': content,
            }
        ],
    }


def post_teams_card(url: str, payload: dict) -> bool:
    """Post a message to a Teams Workflows webhook (run as a background task).

    Returns:
        True if the webhook accepted the message
    """
    try:
        response = requests.post(url, json=payload, timeout=TEAMS_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning('Fleet: Teams notification failed: %s', exc)
        return False

    return True


def send_teams(title, text, link='', severity='INFO', facts=None) -> bool:
    """Queue a Teams notification, if a webhook URL is configured."""
    url = get_global_setting('FLEET_TEAMS_WEBHOOK_URL', cache=False)

    if not url:
        return False

    from InvenTree.tasks import offload_task

    payload = build_teams_card(title, text, link=link, severity=severity, facts=facts)

    return bool(offload_task(post_teams_card, url, payload, group='notification'))


def get_alert_emails() -> list[str]:
    """Return the FLEET_ALERT_EMAILS addresses."""
    value = get_global_setting('FLEET_ALERT_EMAILS', cache=False) or ''
    return [email.strip() for email in value.split(',') if email.strip()]


def send_fleet_email(title, text, link='', facts=None) -> bool:
    """Send a notification email to FLEET_ALERT_EMAILS, if email is configured."""
    from InvenTree.helpers_email import is_email_configured, send_email

    recipients = get_alert_emails()

    if not recipients or not is_email_configured():
        return False

    lines = [text, ''] if text else []
    lines += [f'{name}: {value}' for name, value in facts or [] if value]

    if link:
        lines += ['', link]

    html = f'<h3>{escape(title)}</h3>'

    if text:
        html += f'<p>{escape(text)}</p>'

    if facts:
        html += '<table>'
        html += ''.join(
            f'<tr><th align="left">{escape(str(name))}</th><td>{escape(str(value))}</td></tr>'
            for name, value in facts
            if value
        )
        html += '</table>'

    if link:
        html += f'<p><a href="{escape(link)}">{escape(link)}</a></p>'

    sent, _error = send_email(
        f'[Fleet] {title}', '\n'.join(lines), recipients, html_message=html
    )

    return sent


def get_fleet_users():
    """Return the active users with the fleet view role (and superusers)."""
    return (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(
            Q(is_superuser=True)
            | Q(groups__rule_sets__name='fleet', groups__rule_sets__can_view=True)
        )
        .distinct()
    )


def send_in_app(event_code, title, text, link='', obj=None) -> None:
    """Create InvenTree UI notifications for the fleet users.

    Skipped without an object: InvenTree records each notification against an
    object id.
    """
    from common.notifications import trigger_notification

    if obj is None or obj.pk is None:
        return

    trigger_notification(
        obj,
        category=f'fleet.{event_code}',
        targets=list(get_fleet_users()),
        context={'name': title, 'message': text, 'link': link},
        delivery_methods=[UI_NOTIFICATION_SLUG],
        check_recent=False,
    )


def severity_name(severity) -> str:
    """Return the name (INFO, WARNING or CRITICAL) of an alert severity.

    Arguments:
        severity: An AlertSeverity code, or its name
    """
    from fleet.status_codes import AlertSeverity

    if isinstance(severity, str) and not severity.isdigit():
        return severity.upper()

    try:
        return AlertSeverity(int(severity)).name
    except (TypeError, ValueError):
        return AlertSeverity.INFO.name


def notify(
    event_code: str,
    title: str,
    text: str = '',
    link: str = '',
    severity='INFO',
    obj=None,
    facts=None,
) -> None:
    """Send a fleet notification to Teams, email and the InvenTree UI.

    The notification is sent once the current transaction commits, so that
    nothing is sent for a change which is rolled back.

    Arguments:
        event_code: Event identifier (e.g. 'alert_opened'), used as the UI category
        title: Short title
        text: Message text
        link: Absolute link to open (see build_link)
        severity: AlertSeverity code (or name: INFO, WARNING or CRITICAL)
        obj: Model instance which the notification is about (optional)
        facts: List of (name, value) pairs shown in the message
    """
    severity = severity_name(severity)

    on_commit(lambda: dispatch(event_code, title, text, link, severity, obj, facts))


def dispatch(event_code, title, text, link, severity, obj, facts) -> None:
    """Send a notification on every channel (see notify)."""
    for channel, func, args in [
        ('teams', send_teams, (title, text, link, severity, facts)),
        ('email', send_fleet_email, (title, text, link, facts)),
        ('in-app', send_in_app, (event_code, title, text, link, obj)),
    ]:
        try:
            # A savepoint, so that a failing channel cannot break the caller's transaction
            with transaction.atomic():
                func(*args)
        except Exception:
            logger.exception('Fleet: %s notification failed (%s)', channel, event_code)
