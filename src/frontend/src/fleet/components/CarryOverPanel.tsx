import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Skeleton,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconAnchor,
  IconCircleCheck,
  IconDeviceFloppy,
  IconInfoCircle,
  IconSquareOff
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState
} from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { formatDate, formatDecimal } from '../../defaults/formatters';
import { showApiErrorMessage } from '../../functions/notifications';
import { useUserState } from '../../states/UserState';
import { userDisplayName } from './FleetBadges';

type Pick = { checked: boolean; quantity: number };

/**
 * Colour of a carry-over line status (pending, moved, released)
 */
export function carryOverStatusColor(status: string): string {
  switch (status) {
    case 'MOVED':
      return 'green';
    case 'RELEASED':
      return 'gray';
    default:
      return 'blue';
  }
}

/**
 * "Carry over from <old serial>": the consumed parts of the old device's build
 * order (typically the mooring) which stay at sea and go on the new device.
 *
 * A checklist of the consumed rows with editable quantities, and the shortcuts
 * "All mooring" (the part category of the setting FLEET_MOORING_CATEGORY) and
 * "None". Saving replaces the picks. Editable until the swap task is completed
 * (with the fleet change and stock change roles); afterwards, and for picks
 * which were released, the history is listed.
 *
 * On the deployment page the saved picks are then validated ("Validate
 * Carry-over"): a replacement goes on a trip only once its carry-over is
 * validated, and changing the picks there clears the validation.
 *
 * Used by the replacement deployment page, the swap task page and the portal
 * Work tab of the swap task (pass taskId there).
 */
export function CarryOverPanel({
  deploymentId,
  taskId,
  onChange
}: Readonly<{
  deploymentId?: number;
  taskId?: number;
  onChange?: () => void;
}>): ReactNode {
  const api = useApi();
  const user = useUserState();

  const url = taskId
    ? apiUrl(ApiEndpoints.fleet_task_carry_over, taskId)
    : apiUrl(ApiEndpoints.fleet_deployment_carry_over, deploymentId);

  const query = useQuery({
    queryKey: ['fleet-carry-over', deploymentId, taskId],
    enabled: !!(taskId || deploymentId),
    queryFn: () => api.get(url).then((res) => res.data)
  });

  const data = query.data;

  const [picks, setPicks] = useState<Record<number, Pick>>({});
  const [dirty, setDirty] = useState<boolean>(false);
  const [saving, setSaving] = useState<boolean>(false);

  // Start from what is picked
  useEffect(() => {
    const initial: Record<number, Pick> = {};

    for (const line of data?.lines ?? []) {
      initial[line.stock_item] = {
        checked: line.picked > 0,
        quantity: line.picked > 0 ? line.picked : line.available
      };
    }

    setPicks(initial);
    setDirty(false);
  }, [data]);

  const canEdit: boolean =
    !!data?.editable &&
    user.hasChangeRole(UserRoles.fleet) &&
    user.hasChangeRole(UserRoles.stock);

  const update = useCallback((stockItem: number, change: Partial<Pick>) => {
    setPicks((current) => ({
      ...current,
      [stockItem]: { ...current[stockItem], ...change }
    }));
    setDirty(true);
  }, []);

  const selectMooring = useCallback(() => {
    setPicks((current) => {
      const next = { ...current };

      for (const line of data?.lines ?? []) {
        if (line.mooring && line.available > 0) {
          next[line.stock_item] = { checked: true, quantity: line.available };
        }
      }

      return next;
    });
    setDirty(true);
  }, [data]);

  const selectNone = useCallback(() => {
    setPicks((current) =>
      Object.fromEntries(
        Object.entries(current).map(([key, pick]) => [
          key,
          { ...pick, checked: false }
        ])
      )
    );
    setDirty(true);
  }, []);

  const save = useCallback(() => {
    const lines = (data?.lines ?? [])
      .filter((line: any) => picks[line.stock_item]?.checked)
      .map((line: any) => ({
        stock_item: line.stock_item,
        quantity: picks[line.stock_item].quantity
      }));

    setSaving(true);

    api
      .post(url, { lines: lines })
      .then((response) => {
        query.refetch();
        notifications.show({
          title: t`Carry-over saved`,
          message: t`The new build order counts the picked parts as covered`,
          color: 'green'
        });
        onChange?.();
        return response;
      })
      .catch((error) =>
        showApiErrorMessage({ error: error, title: t`Carry-over not saved` })
      )
      .finally(() => setSaving(false));
  }, [api, url, data, picks, query, onChange]);

  const [validating, setValidating] = useState<boolean>(false);

  const validate = useCallback(() => {
    setValidating(true);

    api
      .post(
        apiUrl(ApiEndpoints.fleet_deployment_carry_over_validate, data?.deployment),
        {}
      )
      .then((response) => {
        query.refetch();
        notifications.show({
          title: t`Carry-over validated`,
          message: t`The replacement can now be added to a trip`,
          color: 'green'
        });
        onChange?.();
        return response;
      })
      .catch((error) =>
        showApiErrorMessage({ error: error, title: t`Carry-over not validated` })
      )
      .finally(() => setValidating(false));
  }, [api, data, query, onChange]);

  const pickedCount = useMemo(
    () => Object.values(picks).filter((pick) => pick.checked).length,
    [picks]
  );

  if (query.isLoading || !data) {
    return <Skeleton height={120} />;
  }

  const lines: any[] = data.lines ?? [];
  const history: any[] = data.history ?? [];

  return (
    <Stack gap='xs'>
      <Group justify='space-between' wrap='wrap'>
        <Title order={5}>
          {data.source_device_serial
            ? t`Carry over from ${data.source_device_serial}`
            : t`Carry over`}
        </Title>
        {data.source_build_reference && data.target_build_reference && (
          <Text size='sm' c='dimmed'>
            {data.source_build_reference} → {data.target_build_reference}
          </Text>
        )}
      </Group>
      <Text size='sm' c='dimmed'>
        {t`Consumed parts of the old device which stay at sea and go on the new device (e.g. the mooring). The new build order counts them as covered. They move to the new build order when the swap task is completed.`}
      </Text>
      {data.reason && (
        <Alert color='blue' icon={<IconInfoCircle />}>
          {data.reason}
        </Alert>
      )}
      {data.validation == 'TO_VALIDATE' && (
        <Alert
          color='orange'
          icon={<IconAlertTriangle />}
          title={t`Carry-over not validated`}
        >
          <Stack gap='xs'>
            <Text size='sm'>
              {t`Check the parts picked below (or pick none), save them, then validate. The replacement can only be added to a trip once its carry-over is validated.`}
            </Text>
            {canEdit && !taskId && (
              <Group gap='xs'>
                <Button
                  size='xs'
                  color='green'
                  leftSection={<IconCircleCheck size={16} />}
                  disabled={dirty}
                  loading={validating}
                  onClick={validate}
                >
                  {t`Validate Carry-over`}
                </Button>
                {dirty && (
                  <Text size='xs' c='dimmed'>
                    {t`Save the changes first`}
                  </Text>
                )}
              </Group>
            )}
          </Stack>
        </Alert>
      )}
      {data.validation == 'VALIDATED' && (
        <Alert
          color='green'
          icon={<IconCircleCheck />}
          title={t`Carry-over validated`}
        >
          <Text size='sm'>
            {[
              userDisplayName(data.validated_by),
              formatDate(data.validated_at, { showTime: true })
            ]
              .filter(Boolean)
              .join(' · ')}
          </Text>
          {canEdit && !taskId && (
            <Text size='xs' c='dimmed'>
              {t`Saving a change to the picks clears the validation: validate again before adding it to a trip.`}
            </Text>
          )}
        </Alert>
      )}
      {lines.length > 0 && (
        <>
          {canEdit && (
            <Group gap='xs' wrap='wrap'>
              <Button
                size='xs'
                variant='outline'
                leftSection={<IconAnchor size={16} />}
                onClick={selectMooring}
              >
                {t`All mooring`}
              </Button>
              <Button
                size='xs'
                variant='outline'
                color='gray'
                leftSection={<IconSquareOff size={16} />}
                onClick={selectNone}
              >
                {t`None`}
              </Button>
            </Group>
          )}
          <Table.ScrollContainer minWidth={560}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th />
                  <Table.Th>{t`Part`}</Table.Th>
                  <Table.Th>{t`Consumed`}</Table.Th>
                  <Table.Th>{t`Carry Over`}</Table.Th>
                  <Table.Th>{t`New Build Order Line`}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {lines.map((line: any) => {
                  const pick = picks[line.stock_item] ?? {
                    checked: false,
                    quantity: line.available
                  };
                  const unavailable = line.available <= 0 && !pick.checked;

                  return (
                    <Table.Tr key={line.stock_item}>
                      <Table.Td>
                        <Checkbox
                          aria-label={line.part_detail?.full_name}
                          checked={pick.checked}
                          disabled={!canEdit || unavailable}
                          onChange={(event) =>
                            update(line.stock_item, {
                              checked: event.currentTarget.checked
                            })
                          }
                        />
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap='nowrap'>
                          <Text size='sm'>{line.part_detail?.full_name}</Text>
                          {line.mooring && (
                            <Badge size='xs' variant='light'>
                              {t`Mooring`}
                            </Badge>
                          )}
                        </Group>
                        {line.batch && (
                          <Text size='xs' c='dimmed'>
                            {t`Batch`}: {line.batch}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>{formatDecimal(line.quantity)}</Text>
                        {line.available < line.quantity && (
                          <Text size='xs' c='orange'>
                            {t`Picked elsewhere`}:{' '}
                            {formatDecimal(line.quantity - line.available)}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <NumberInput
                          size='xs'
                          w={110}
                          min={0}
                          max={line.available}
                          value={pick.quantity}
                          disabled={!canEdit || !pick.checked}
                          onChange={(value) =>
                            update(line.stock_item, {
                              quantity: Number(value) || 0
                            })
                          }
                        />
                      </Table.Td>
                      <Table.Td>
                        <Text
                          size='sm'
                          c={line.target_line ? undefined : 'dimmed'}
                        >
                          {line.target_line
                            ? line.target_part_name
                            : t`New line on ${data.target_build_reference}`}
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {canEdit && (
            <Group gap='xs'>
              <Button
                leftSection={<IconDeviceFloppy size={18} />}
                disabled={!dirty}
                loading={saving}
                onClick={save}
              >
                {t`Save`}
              </Button>
              <Text size='sm' c='dimmed'>
                {t`Picked`}: {pickedCount}
              </Text>
            </Group>
          )}
          {data.editable && !canEdit && (
            <Text size='xs' c='dimmed'>
              {t`Changing the carry-over needs the fleet change and stock change permissions.`}
            </Text>
          )}
        </>
      )}
      {history.length > 0 && (
        <Table.ScrollContainer minWidth={480}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`Status`}</Table.Th>
                <Table.Th>{t`Part`}</Table.Th>
                <Table.Th>{t`Quantity`}</Table.Th>
                <Table.Th>{t`Task`}</Table.Th>
                <Table.Th>{t`Date`}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {history.map((line: any) => (
                <Table.Tr key={line.pk}>
                  <Table.Td>
                    <Badge color={carryOverStatusColor(line.status)}>
                      {line.status_text}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{line.part_detail?.full_name}</Table.Td>
                  <Table.Td>{formatDecimal(line.quantity)}</Table.Td>
                  <Table.Td>{line.swap_task_reference}</Table.Td>
                  <Table.Td>{formatDate(line.closed_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Stack>
  );
}
