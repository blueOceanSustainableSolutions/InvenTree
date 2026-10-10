import { t } from '@lingui/core/macro';
import {
  Badge,
  Group,
  Paper,
  Stack,
  Text,
  UnstyledButton
} from '@mantine/core';
import { IconChevronRight } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

import { ModelType } from '@lib/enums/ModelType';
import { StatusRenderer } from '../../components/render/StatusRenderer';
import { formatDate } from '../../defaults/formatters';
import { taskTypeLabel } from '../../fleet/components/FleetBadges';

/**
 * A card which opens a screen when tapped: the whole card is the touch target
 */
export function LinkCard({
  to,
  ariaLabel,
  children
}: Readonly<{
  to: string;
  ariaLabel?: string;
  children: ReactNode;
}>): ReactNode {
  const navigate = useNavigate();

  return (
    <UnstyledButton
      w='100%'
      aria-label={ariaLabel}
      onClick={() => navigate(to)}
    >
      <Paper withBorder p='sm' mih={64}>
        <Group justify='space-between' wrap='nowrap' gap='xs'>
          <Stack gap={4} style={{ minWidth: 0, flex: 1 }}>
            {children}
          </Stack>
          <IconChevronRight style={{ flexShrink: 0 }} />
        </Group>
      </Paper>
    </UnstyledButton>
  );
}

/**
 * A maintenance task: reference, status, type, site nickname (else serial),
 * description and dates. Opens the task screen.
 */
export function TaskCard({ task }: Readonly<{ task: any }>): ReactNode {
  const date = task.scheduled_date ?? task.due_date;

  return (
    <LinkCard to={`/task/${task.pk}`} ariaLabel={`task-card-${task.reference}`}>
      <Group gap='xs' justify='space-between'>
        <Text fw={700}>{task.reference}</Text>
        <Group gap={4}>
          {task.overdue && (
            <Badge color='red' size='sm'>
              {t`Overdue`}
            </Badge>
          )}
          <StatusRenderer
            status={task.status_custom_key || task.status}
            type={ModelType.maintenancetask}
          />
        </Group>
      </Group>
      <Text size='sm' fw={500} lineClamp={1}>
        {task.display_name}
      </Text>
      <Text size='sm' c='dimmed' lineClamp={2}>
        {[taskTypeLabel(task.task_type), task.description]
          .filter(Boolean)
          .join(' · ')}
      </Text>
      <Group gap='xs'>
        {date && (
          <Text size='xs' c={task.overdue ? 'red' : 'dimmed'}>
            {task.scheduled_date ? t`Scheduled` : t`Due`}: {formatDate(date)}
          </Text>
        )}
        {task.trip_reference && (
          <Text size='xs' c='dimmed'>
            {task.trip_reference}
          </Text>
        )}
      </Group>
    </LinkCard>
  );
}

/**
 * A field trip: reference, title, status, dates, vessel and task counts.
 * Opens the trip screen.
 */
export function TripCard({ trip }: Readonly<{ trip: any }>): ReactNode {
  const dates = [trip.start_date, trip.end_date]
    .filter(Boolean)
    .map((date: string) => formatDate(date))
    .join(' - ');

  return (
    <LinkCard
      to={`/trips/${trip.pk}`}
      ariaLabel={`trip-card-${trip.reference}`}
    >
      <Group gap='xs' justify='space-between'>
        <Text fw={700}>{trip.reference}</Text>
        <StatusRenderer
          status={trip.status_custom_key || trip.status}
          type={ModelType.fieldtrip}
        />
      </Group>
      <Text size='sm' fw={500} lineClamp={1}>
        {trip.title}
      </Text>
      <Text size='xs' c='dimmed'>
        {[dates, trip.vessel].filter(Boolean).join(' · ')}
      </Text>
      <Text size='xs' c='dimmed'>
        {t`Tasks`}: {trip.task_count ?? 0} ({t`open`}:{' '}
        {trip.open_task_count ?? 0})
      </Text>
    </LinkCard>
  );
}

/** A titled list of cards, with a message when it is empty */
export function CardList({
  title,
  empty,
  children
}: Readonly<{
  title?: ReactNode;
  empty: string;
  children: ReactNode[];
}>): ReactNode {
  return (
    <Stack gap='xs'>
      {title && (
        <Text fw={700} size='lg'>
          {title}
        </Text>
      )}
      {children.length == 0 ? (
        <Text size='sm' c='dimmed'>
          {empty}
        </Text>
      ) : (
        children
      )}
    </Stack>
  );
}
