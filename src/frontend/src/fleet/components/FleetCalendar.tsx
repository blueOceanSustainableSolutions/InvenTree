import type { DatesSetArg, EventClickArg } from '@fullcalendar/core';
import { Group, Text } from '@mantine/core';
import dayjs from 'dayjs';
import { type ReactNode, useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import type { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl, navigateToLink } from '@lib/functions/Navigation';
import Calendar from '../../components/calendar/Calendar';
import {
  StatusRenderer,
  getStatusColor
} from '../../components/render/StatusRenderer';
import useCalendar from '../../hooks/UseCalendar';

/**
 * Fleet calendar (from `fleet/calendar/`): pipeline deployments on their
 * target date, open tasks on their scheduled (else due) date, and field trips
 * from their start to their end date.
 */
export function FleetCalendar(): ReactNode {
  const navigate = useNavigate();

  const [range, setRange] = useState<{ start?: string; end?: string }>({});

  const calendarState = useCalendar({
    endpoint: ApiEndpoints.fleet_calendar,
    name: 'fleet-calendar',
    queryParams: range
  });

  const events = useMemo(
    () =>
      (calendarState.data ?? []).map((event: any) => {
        const color = getStatusColor(event.model_type, event.status);

        return {
          id: `${event.model_type}-${event.pk}`,
          title: event.title,
          start: event.start,
          // FullCalendar end dates are exclusive
          end: event.end
            ? dayjs(event.end).add(1, 'day').format('YYYY-MM-DD')
            : undefined,
          allDay: true,
          editable: false,
          backgroundColor: color,
          borderColor: color,
          extendedProps: event
        };
      }),
    [calendarState.data]
  );

  const onDatesSet = useCallback((info: DatesSetArg) => {
    setRange({
      start: dayjs(info.start).format('YYYY-MM-DD'),
      end: dayjs(info.end).format('YYYY-MM-DD')
    });
  }, []);

  const onClickEvent = useCallback(
    (info: EventClickArg) => {
      const event = info.event.extendedProps;

      if (event?.model_type && event?.pk) {
        navigateToLink(
          getDetailUrl(event.model_type as ModelType, event.pk),
          navigate,
          info.jsEvent
        );
      }
    },
    [navigate]
  );

  const renderEvent = useCallback((info: any) => {
    const event = info.event.extendedProps;

    return (
      <Group gap='xs' wrap='nowrap' style={{ paddingLeft: 5 }}>
        <Text size='sm' fw={700}>
          {info.event.title}
        </Text>
        {event?.model_type && (
          <StatusRenderer
            status={event.status}
            type={event.model_type as ModelType}
          />
        )}
      </Group>
    );
  }, []);

  return (
    <Calendar
      enableRefresh
      events={events}
      state={calendarState}
      editable={false}
      eventContent={renderEvent}
      eventClick={onClickEvent}
      datesSet={onDatesSet}
    />
  );
}
