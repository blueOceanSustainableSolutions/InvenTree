import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  List,
  Paper,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconArrowBackUp,
  IconCircleCheck
} from '@tabler/icons-react';
import { type ReactNode, useCallback, useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { ApiFormFieldType } from '@lib/types/Forms';
import { StandaloneField } from '../../components/forms/StandaloneField';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';

/** Picker for the destination of one stock item */
function DestinationField({
  item,
  onChange
}: Readonly<{
  item: any;
  onChange: (pk: number, location: number | null) => void;
}>): ReactNode {
  const field: ApiFormFieldType = useMemo(
    () => ({
      field_type: 'related field',
      api_url: apiUrl(ApiEndpoints.stock_location_list),
      model: ModelType.stocklocation,
      required: false,
      filters: { structural: false },
      onValueChange: (value: any) => onChange(item.pk, value ?? null)
    }),
    [item.pk, onChange]
  );

  return (
    <StandaloneField
      fieldName={`destination-${item.pk}`}
      fieldDefinition={field}
      hideLabels
    />
  );
}

/** One table of stock to return, with an optional destination per item */
function ReturnTable({
  items,
  defaultText,
  onChange
}: Readonly<{
  items: any[];
  defaultText: (item: any) => string;
  onChange: (pk: number, location: number | null) => void;
}>): ReactNode {
  if (!items.length) {
    return <Text size='sm' c='dimmed'>{t`Nothing`}</Text>;
  }

  return (
    <Table striped>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t`Part`}</Table.Th>
          <Table.Th>{t`Stock`}</Table.Th>
          <Table.Th>{t`Default Destination`}</Table.Th>
          <Table.Th style={{ minWidth: 260 }}>{t`Other Destination`}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {items.map((item: any) => (
          <Table.Tr key={item.pk}>
            <Table.Td>{item.part_name}</Table.Td>
            <Table.Td>
              {item.serial ? `#${item.serial}` : item.quantity}
              {item.batch ? ` (${item.batch})` : ''}
            </Table.Td>
            <Table.Td>
              <Text size='sm' c={defaultText(item) ? undefined : 'orange'}>
                {defaultText(item) || t`None: choose a destination`}
              </Text>
            </Table.Td>
            <Table.Td>
              <DestinationField item={item} onChange={onChange} />
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

/** Result of the last reconcile */
function ReconcileResult({ result }: Readonly<{ result: any }>): ReactNode {
  if (result.closed) {
    return (
      <Alert color='green' icon={<IconCircleCheck />} title={t`Trip closed`}>
        {t`Returned to stock`}: {result.returned} · {t`To the workshop`}:{' '}
        {result.to_workshop} · {t`Tasks released`}: {result.released}
      </Alert>
    );
  }

  return (
    <Alert
      color='orange'
      icon={<IconAlertTriangle />}
      title={t`The trip is not closed yet`}
    >
      <Stack gap='xs'>
        <Text size='sm'>
          {t`Returned to stock`}: {result.returned} · {t`To the workshop`}:{' '}
          {result.to_workshop} · {t`Tasks released`}: {result.released}
        </Text>
        {result.missing_location?.length > 0 && (
          <>
            <Text size='sm' fw={500}>
              {t`These items have no default location: choose a destination and reconcile again`}
            </Text>
            <List size='sm'>
              {result.missing_location.map((item: string) => (
                <List.Item key={item}>{item}</List.Item>
              ))}
            </List>
          </>
        )}
        {result.open_tasks?.length > 0 && (
          <>
            <Text size='sm' fw={500}>
              {t`These tasks are still in progress: complete or cancel them first`}
            </Text>
            <List size='sm'>
              {result.open_tasks.map((item: string) => (
                <List.Item key={item}>{item}</List.Item>
              ))}
            </List>
          </>
        )}
      </Stack>
    </Alert>
  );
}

/**
 * Reconcile a field trip: the kit leftovers go back to stock (part default
 * location, unless another destination is chosen), removed components go to
 * the workshop, tasks which were not started go back to proposed, and the
 * trip is closed when nothing is left.
 */
export function TripReconcilePanel({
  trip,
  kit,
  canReconcile,
  onChange
}: Readonly<{
  trip: any;
  kit: any;
  canReconcile: boolean;
  onChange: () => void;
}>): ReactNode {
  const user = useUserState();

  const [destinations, setDestinations] = useState<
    Record<number, number | null>
  >({});
  const [result, setResult] = useState<any>(null);

  const allowed =
    canReconcile &&
    user.hasChangeRole(UserRoles.fleet) &&
    user.hasChangeRole(UserRoles.stock);

  const setDestination = useCallback((pk: number, location: number | null) => {
    setDestinations((current) => ({ ...current, [pk]: location }));
  }, []);

  const returns = useMemo(
    () =>
      Object.entries(destinations)
        .filter(([, location]) => !!location)
        .map(([pk, location]) => ({
          stock_item: Number(pk),
          location: location
        })),
    [destinations]
  );

  const contents: any[] = kit?.contents ?? [];
  const removed: any[] = kit?.removed ?? [];

  const reconcile = useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_trip_reconcile, trip.pk),
    title: t`Reconcile Trip`,
    preFormContent: (
      <Stack gap='xs'>
        <Text size='sm'>
          {t`Kit leftovers`}: {contents.length} · {t`Removed components`}:{' '}
          {removed.length} · {t`Other destinations`}: {returns.length}
        </Text>
        <Text size='sm' c='dimmed'>
          {t`Tasks which were not started go back to proposed. The trip closes when the kit is empty and no task is in progress.`}
        </Text>
      </Stack>
    ),
    processFormData: () => ({ returns: returns }),
    submitText: t`Reconcile`,
    successMessage: null,
    onFormSuccess: (data: any) => {
      setResult(data?.reconcile ?? null);
      setDestinations({});
      onChange();
    }
  });

  return (
    <>
      {reconcile.modal}
      <Stack gap='sm'>
        {result && <ReconcileResult result={result} />}
        {!canReconcile && (
          <Alert color='blue'>
            {t`Only a trip with a prepared kit, in progress or reconciling can be reconciled.`}
          </Alert>
        )}
        <Paper withBorder p='sm'>
          <Stack gap='xs'>
            <Title order={5}>{t`Kit Leftovers`}</Title>
            <Text size='sm' c='dimmed'>
              {t`Stock left in the kit goes back to the part default location, unless you choose another destination.`}
            </Text>
            <ReturnTable
              items={contents}
              defaultText={(item: any) =>
                item.default_location ? t`Part default location` : ''
              }
              onChange={setDestination}
            />
          </Stack>
        </Paper>
        <Paper withBorder p='sm'>
          <Stack gap='xs'>
            <Title order={5}>{t`Removed Components`}</Title>
            <Text size='sm' c='dimmed'>
              {t`Components removed in the field go to the fleet workshop, unless you choose another destination.`}
            </Text>
            <ReturnTable
              items={removed}
              defaultText={() => t`Fleet workshop`}
              onChange={setDestination}
            />
          </Stack>
        </Paper>
        <Group>
          <Button
            color='green'
            leftSection={<IconArrowBackUp size={18} />}
            disabled={!allowed}
            onClick={() => reconcile.open()}
          >
            {t`Reconcile`}
          </Button>
          {canReconcile && !user.hasChangeRole(UserRoles.stock) && (
            <Text size='xs' c='dimmed'>
              {t`Reconciling also needs the stock change permission.`}
            </Text>
          )}
        </Group>
      </Stack>
    </>
  );
}
