import { t } from '@lingui/core/macro';
import { Anchor, Skeleton, Text, Timeline } from '@mantine/core';
import {
  IconAlertTriangle,
  IconAnchor,
  IconArrowBackUp,
  IconCalendarPlus,
  IconTool
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import useStatusCodes from '../../hooks/UseStatusCodes';
import {
  FleetStatus,
  alertTypeLabel,
  fleetStatusColor,
  taskTypeLabel
} from './FleetBadges';

/** One event of the history of a deployment */
type TimelineEvent = {
  key: string;
  date: string;
  title: string;
  text?: string;
  color: string;
  icon: ReactNode;
  link?: string;
};

/** Number of alerts shown in the history (the most recent ones) */
const ALERT_LIMIT = 25;

/**
 * History of a deployment, newest first: planned, deployed, the completed
 * maintenance tasks, the alerts and the recovery.
 */
export function DeploymentTimeline({
  deployment
}: Readonly<{ deployment: any }>): ReactNode {
  const api = useApi();
  const navigate = useNavigate();
  const taskStatus = useStatusCodes({ modelType: ModelType.maintenancetask });

  const tasksQuery = useQuery({
    queryKey: [
      'fleet-deployment-timeline-tasks',
      deployment.pk,
      taskStatus.COMPLETED
    ],
    enabled: !!deployment.pk && taskStatus.COMPLETED !== undefined,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_task_list), {
          params: { deployment: deployment.pk, status: taskStatus.COMPLETED }
        })
        .then((response) => response.data ?? [])
  });

  const alertsQuery = useQuery({
    queryKey: ['fleet-deployment-timeline-alerts', deployment.pk],
    enabled: !!deployment.pk,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_alert_list), {
          params: {
            deployment: deployment.pk,
            ordering: '-opened_at',
            limit: ALERT_LIMIT
          }
        })
        .then((response) => response.data?.results ?? [])
  });

  const events: TimelineEvent[] = useMemo(() => {
    const result: TimelineEvent[] = [];

    if (deployment.creation_date) {
      result.push({
        key: 'created',
        date: deployment.creation_date,
        title: t`Planned`,
        text: deployment.reference,
        color: 'gray',
        icon: <IconCalendarPlus size={14} />
      });
    }

    if (deployment.deployed_at) {
      result.push({
        key: 'deployed',
        date: deployment.deployed_at,
        title: t`Deployed`,
        text: deployment.site_detail?.name,
        color: 'green',
        icon: <IconAnchor size={14} />
      });
    }

    for (const task of tasksQuery.data ?? []) {
      result.push({
        key: `task-${task.pk}`,
        date: task.completed_at,
        title: `${task.reference}: ${taskTypeLabel(task.task_type)}`,
        text: task.summary || task.description,
        color: 'blue',
        icon: <IconTool size={14} />,
        link: getDetailUrl(ModelType.maintenancetask, task.pk)
      });
    }

    for (const alert of alertsQuery.data ?? []) {
      result.push({
        key: `alert-${alert.pk}`,
        date: alert.opened_at,
        title: `${alert.reference}: ${alertTypeLabel(alert.alert_type)}`,
        text: alert.resolved_at
          ? `${alert.message} (${t`resolved`} ${formatDate(alert.resolved_at)})`
          : alert.message,
        color: fleetStatusColor(FleetStatus.severity, alert.severity),
        icon: <IconAlertTriangle size={14} />
      });
    }

    if (deployment.recovered_at) {
      result.push({
        key: 'recovered',
        date: deployment.recovered_at,
        title: t`Recovered`,
        color: 'dark',
        icon: <IconArrowBackUp size={14} />
      });
    }

    return result
      .filter((event) => !!event.date)
      .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  }, [deployment, tasksQuery.data, alertsQuery.data]);

  if (tasksQuery.isLoading || alertsQuery.isLoading) {
    return <Skeleton h={120} />;
  }

  return (
    <Timeline bulletSize={24} lineWidth={2}>
      {events.map((event) => (
        <Timeline.Item
          key={event.key}
          bullet={event.icon}
          color={event.color}
          title={
            event.link ? (
              <Anchor
                size='sm'
                fw={700}
                onClick={() => event.link && navigate(event.link)}
              >
                {event.title}
              </Anchor>
            ) : (
              event.title
            )
          }
        >
          {event.text && (
            <Text size='sm' c='dimmed'>
              {event.text}
            </Text>
          )}
          <Text size='xs' c='dimmed'>
            {formatDate(event.date, { showTime: event.date.length > 10 })}
          </Text>
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
