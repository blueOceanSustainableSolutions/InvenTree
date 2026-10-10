import type { EventChangeArg, EventClickArg } from '@fullcalendar/core';
import { t } from '@lingui/core/macro';
import { Group, Text } from '@mantine/core';
import { hideNotification, showNotification } from '@mantine/notifications';
import { IconCircleCheck, IconExclamationCircle } from '@tabler/icons-react';
import dayjs from 'dayjs';
import { type ReactNode, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl, navigateToLink } from '@lib/functions/Navigation';
import type { TableFilter } from '@lib/types/Filters';
import Calendar from '../../components/calendar/Calendar';
import {
  StatusRenderer,
  getStatusColor
} from '../../components/render/StatusRenderer';
import { useApi } from '../../contexts/ApiContext';
import useCalendar from '../../hooks/UseCalendar';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { useUserState } from '../../states/UserState';
import { AssignedToMeFilter, StatusFilterOptions } from '../../tables/Filter';
import { taskTypeChoices } from './FleetBadges';

/**
 * Calendar of maintenance tasks, on their scheduled date (else their due
 * date). Dragging a task sets its scheduled date.
 */
export function TaskCalendar({
  params = {}
}: Readonly<{ params?: Record<string, any> }>): ReactNode {
  const api = useApi();
  const user = useUserState();
  const navigate = useNavigate();
  const taskStatus = useStatusCodes({ modelType: ModelType.maintenancetask });

  const canEdit = user.hasChangeRole(UserRoles.fleet);

  const filters: TableFilter[] = useMemo(
    () => [
      {
        name: 'open',
        label: t`Open`,
        description: t`Show tasks which are proposed, scheduled or in progress`
      },
      {
        name: 'status',
        label: t`Status`,
        description: t`Filter by task status`,
        choiceFunction: StatusFilterOptions(ModelType.maintenancetask)
      },
      {
        name: 'task_type',
        label: t`Type`,
        description: t`Filter by task type`,
        choices: taskTypeChoices()
      },
      {
        ...AssignedToMeFilter(),
        description: t`Show the tasks I work on, or which are on my trips`
      }
    ],
    []
  );

  const calendarState = useCalendar({
    endpoint: ApiEndpoints.fleet_task_list,
    name: 'fleet-tasks',
    queryParams: params
  });

  const events = useMemo(
    () =>
      (calendarState.data ?? [])
        .filter((task: any) => task.scheduled_date || task.due_date)
        .map((task: any) => {
          const color = getStatusColor(ModelType.maintenancetask, task.status);
          const open = [
            taskStatus.PROPOSED,
            taskStatus.SCHEDULED,
            taskStatus.IN_PROGRESS
          ].includes(task.status);

          return {
            id: task.pk,
            title: task.reference,
            start: task.scheduled_date || task.due_date,
            allDay: true,
            startEditable: canEdit && open,
            durationEditable: false,
            backgroundColor: color,
            borderColor: color
          };
        }),
    [calendarState.data, canEdit, taskStatus]
  );

  const onChangeTask = useCallback(
    (info: EventChangeArg) => {
      if (!info.event.start) {
        return;
      }

      api
        .patch(apiUrl(ApiEndpoints.fleet_task_list, info.event.id), {
          scheduled_date: dayjs(info.event.start).format('YYYY-MM-DD')
        })
        .then(() => {
          hideNotification('fleet-calendar-edit');
          showNotification({
            id: 'fleet-calendar-edit',
            message: t`Task scheduled`,
            color: 'green',
            icon: <IconCircleCheck />
          });
          calendarState.query.refetch();
        })
        .catch(() => {
          info.revert();
          hideNotification('fleet-calendar-edit');
          showNotification({
            id: 'fleet-calendar-edit',
            message: t`Error scheduling the task`,
            color: 'red',
            icon: <IconExclamationCircle />
          });
        });
    },
    [api, calendarState.query]
  );

  const onClickTask = useCallback(
    (info: EventClickArg) => {
      if (info.event.id) {
        navigateToLink(
          getDetailUrl(ModelType.maintenancetask, info.event.id),
          navigate,
          info.jsEvent
        );
      }
    },
    [navigate]
  );

  const renderTask = useCallback(
    (event: any) => {
      const task = calendarState.data?.find(
        (item: any) => item.pk.toString() == event.event.id.toString()
      );

      if (!task) {
        return event.event.title;
      }

      return (
        <Group gap='xs' wrap='nowrap' style={{ paddingLeft: 5 }}>
          <Text size='sm' fw={700}>
            {task.reference}
          </Text>
          <Text size='xs'>{task.display_name}</Text>
          <StatusRenderer
            status={task.status}
            type={ModelType.maintenancetask}
          />
        </Group>
      );
    },
    [calendarState.data]
  );

  return (
    <Calendar
      enableDownload
      enableFilters
      enableRefresh
      enableSearch
      events={events}
      state={calendarState}
      filters={filters}
      editable={canEdit}
      eventContent={renderTask}
      eventClick={onClickTask}
      eventChange={onChangeTask}
    />
  );
}
