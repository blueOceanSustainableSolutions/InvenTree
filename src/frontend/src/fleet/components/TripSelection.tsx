import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { showNotification } from '@mantine/notifications';
import { IconRoute, IconSailboat } from '@tabler/icons-react';
import {
  type ReactElement,
  type ReactNode,
  useCallback,
  useMemo,
  useRef,
  useState
} from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { ApiFormFieldType } from '@lib/types/Forms';
import type { TableState } from '@lib/types/Tables';
import { StandaloneField } from '../../components/forms/StandaloneField';
import { useApi } from '../../contexts/ApiContext';
import { useTripFields } from '../../forms/FleetForms';
import { showApiErrorMessage } from '../../functions/notifications';
import { useCreateApiFormModal } from '../../hooks/UseForm';
import { useUserState } from '../../states/UserState';
import { createTeamErrorStore, teamErrorMessage } from './TeamSelect';

/** No team selected yet (stable reference) */
const NO_TEAM: number[] = [];

/** What a plan selection contains */
export type TripSelectionKind = 'tasks' | 'deployments';

/**
 * Form to create a field trip, optionally with a selection of tasks or
 * ready deployments (sent as the write-only `tasks` / `deployments` fields).
 */
export function useNewTripForm({
  selection,
  preFormContent,
  onFormSuccess
}: {
  selection?: () => Record<string, number[]>;
  preFormContent?: ReactElement;
  onFormSuccess?: () => void;
}) {
  const team = useRef<number[]>([]);
  const [teamErrors] = useState(createTeamErrorStore);

  const onTeamChange = useCallback((value: number[]) => {
    team.current = value;
  }, []);

  const fields = useTripFields({
    initialTeam: NO_TEAM,
    onTeamChange,
    teamErrors
  });

  const form = useCreateApiFormModal({
    url: ApiEndpoints.fleet_trip_list,
    title: t`New Field Trip`,
    fields: fields,
    preFormContent: preFormContent,
    processFormData: (data: any) => ({
      ...data,
      team: team.current,
      ...(selection?.() ?? {})
    }),
    onFormSuccess: () => onFormSuccess?.(),
    onFormError: (error: any) => teamErrors.set(teamErrorMessage(error)),
    follow: true,
    modelType: ModelType.fieldtrip
  });

  const open = useCallback(() => {
    team.current = [];
    teamErrors.set(undefined);
    form.open();
  }, [form, teamErrors]);

  return { modal: form.modal, open };
}

/**
 * Modal to add a selection of tasks or ready deployments to an open trip
 */
export function AddToTripModal({
  opened,
  onClose,
  kind,
  ids,
  onAdded
}: Readonly<{
  opened: boolean;
  onClose: () => void;
  kind: TripSelectionKind;
  ids: number[];
  onAdded: (trip: any) => void;
}>): ReactNode {
  const api = useApi();

  const [trip, setTrip] = useState<number | null>(null);
  const [saving, setSaving] = useState<boolean>(false);

  const tripField: ApiFormFieldType = useMemo(
    () => ({
      field_type: 'related field',
      api_url: apiUrl(ApiEndpoints.fleet_trip_list),
      model: ModelType.fieldtrip,
      label: t`Field Trip`,
      description: t`An open field trip`,
      required: true,
      filters: { open: true },
      onValueChange: (value: any) => setTrip(value ?? null)
    }),
    []
  );

  const close = useCallback(() => {
    setTrip(null);
    onClose();
  }, [onClose]);

  const submit = useCallback(() => {
    if (!trip) {
      return;
    }

    setSaving(true);

    api
      .post(apiUrl(ApiEndpoints.fleet_trip_add_tasks, trip), { [kind]: ids })
      .then((response) => {
        showNotification({
          title: t`Success`,
          message: t`Added to the field trip`,
          color: 'green'
        });
        onAdded(response.data);
        close();
      })
      .catch((error) =>
        showApiErrorMessage({ error: error, title: t`Add to Trip` })
      )
      .finally(() => setSaving(false));
  }, [api, trip, kind, ids, onAdded, close]);

  return (
    <Modal opened={opened} onClose={close} title={t`Add to Trip`}>
      <Stack gap='sm'>
        <Text size='sm'>
          {kind == 'tasks'
            ? t`Selected tasks`
            : t`Selected deployments (each gets a deployment or swap task)`}
          : {ids.length}
        </Text>
        {opened && <StandaloneField fieldDefinition={tripField} />}
        <Group justify='right'>
          <Button variant='outline' onClick={close}>
            {t`Cancel`}
          </Button>
          <Button
            color='green'
            disabled={!trip || ids.length == 0}
            loading={saving}
            onClick={submit}
          >
            {t`Add to Trip`}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/**
 * Plan selection: table actions "Create field trip" and "Add to trip" for
 * the selected rows of a task table (open tasks) or of the pipeline table
 * (ready deployments).
 */
export function useTripSelectionActions({
  table,
  kind,
  enabled = true
}: {
  table: TableState;
  kind: TripSelectionKind;
  enabled?: boolean;
}) {
  const user = useUserState();

  const [addOpened, setAddOpened] = useState<boolean>(false);

  const ids: number[] = table.selectedIds;

  const newTrip = useNewTripForm({
    selection: () => ({ [kind]: table.selectedIds }),
    preFormContent: (
      <Alert color='blue' icon={<IconSailboat />}>
        {kind == 'tasks'
          ? t`The selected tasks are added to the new trip`
          : t`The selected deployments are added to the new trip`}
        : {ids.length}
      </Alert>
    ),
    onFormSuccess: () => table.clearSelectedRecords()
  });

  const onAdded = useCallback(() => {
    table.clearSelectedRecords();
    table.refreshTable();
  }, [table]);

  const actions: ReactNode[] = useMemo(() => {
    if (!enabled) {
      return [];
    }

    const result: ReactNode[] = [];

    if (user.hasAddRole(UserRoles.fleet)) {
      result.push(
        <Button
          key='create-trip'
          variant='outline'
          leftSection={<IconSailboat size={18} />}
          disabled={!table.hasSelectedRecords}
          onClick={() => newTrip.open()}
        >
          {t`Create Field Trip`}
        </Button>
      );
    }

    if (user.hasChangeRole(UserRoles.fleet)) {
      result.push(
        <Button
          key='add-to-trip'
          variant='outline'
          leftSection={<IconRoute size={18} />}
          disabled={!table.hasSelectedRecords}
          onClick={() => setAddOpened(true)}
        >
          {t`Add to Trip`}
        </Button>
      );
    }

    return result;
  }, [enabled, user, table.hasSelectedRecords, newTrip]);

  const modals = enabled ? (
    <>
      {newTrip.modal}
      <AddToTripModal
        opened={addOpened}
        onClose={() => setAddOpened(false)}
        kind={kind}
        ids={ids}
        onAdded={onAdded}
      />
    </>
  ) : null;

  return { actions, modals };
}
