import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Group,
  Paper,
  SegmentedControl,
  Skeleton,
  Stack,
  Text,
  TextInput,
  Tooltip
} from '@mantine/core';
import { IconEdit, IconTool } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useCallback, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { checklistResultFields } from '../../forms/FleetForms';
import { showApiErrorMessage } from '../../functions/notifications';
import { useEditApiFormModal } from '../../hooks/UseForm';
import { checklistResultInfo } from './FleetBadges';

/**
 * One checklist item: OK / Issue / N/A, a value for measurements, and a
 * fault code and an optional action for an issue
 */
function ChecklistRow({
  row,
  editable,
  onResult,
  onIssue,
  onEdit,
  onAction
}: Readonly<{
  row: any;
  editable: boolean;
  onResult: (row: any, data: Record<string, any>) => void;
  onIssue: (row: any) => void;
  onEdit: (row: any) => void;
  onAction?: (row: any) => void;
}>): ReactNode {
  const [value, setValue] = useState<string>(row.value ?? '');
  const info = checklistResultInfo(row.result);
  const pending = row.result == 'PENDING';

  return (
    <Paper withBorder p='xs'>
      <Stack gap={4}>
        <Group justify='space-between' wrap='nowrap' align='flex-start'>
          <Stack gap={0}>
            <Text size='sm' fw={500}>
              {row.text}
              {row.required && pending && (
                <Text span c='red'>
                  {' '}
                  *
                </Text>
              )}
            </Text>
            {row.kind == 'PHOTO' && (
              <Text size='xs' c='dimmed'>
                {t`Add the photo as an attachment of the task`}
              </Text>
            )}
          </Stack>
          {!editable && (
            <Badge color={info.color} variant='light'>
              {info.label}
            </Badge>
          )}
        </Group>
        {editable && (
          <Group gap='xs' wrap='wrap'>
            <SegmentedControl
              size='sm'
              value={pending ? '' : row.result}
              onChange={(result: string) => {
                if (result == 'ISSUE') {
                  onIssue(row);
                } else {
                  onResult(row, { result: result });
                }
              }}
              data={[
                { value: 'OK', label: t`OK` },
                { value: 'ISSUE', label: t`Issue` },
                { value: 'NA', label: t`N/A` }
              ]}
              color={pending ? undefined : info.color}
            />
            {row.kind == 'MEASUREMENT' && (
              <TextInput
                size='sm'
                w={140}
                placeholder={t`Value`}
                rightSection={
                  row.unit ? (
                    <Text size='xs' c='dimmed'>
                      {row.unit}
                    </Text>
                  ) : undefined
                }
                value={value}
                onChange={(event) => setValue(event.currentTarget.value)}
                onBlur={() => {
                  if (value != (row.value ?? '')) {
                    onResult(row, { value: value });
                  }
                }}
              />
            )}
            <Tooltip label={t`Details`}>
              <ActionIcon variant='subtle' onClick={() => onEdit(row)}>
                <IconEdit size={18} />
              </ActionIcon>
            </Tooltip>
            {row.result == 'ISSUE' && onAction && (
              <Tooltip label={t`Record an action for this issue`}>
                <ActionIcon
                  variant='subtle'
                  color='orange'
                  onClick={() => onAction(row)}
                >
                  <IconTool size={18} />
                </ActionIcon>
              </Tooltip>
            )}
          </Group>
        )}
        {!editable && row.value && (
          <Text size='sm'>
            {row.value} {row.unit ?? ''}
          </Text>
        )}
        {(row.fault_code_detail || row.note) && (
          <Text size='xs' c='dimmed'>
            {[row.fault_code_detail?.name, row.note]
              .filter(Boolean)
              .join(' - ')}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

/**
 * The checklist of a maintenance task.
 *
 * Items come from the device type checklist template when the task starts.
 * They can be answered while the task is in progress; every required item
 * must be answered before the task can be closed.
 */
export function ChecklistEditor({
  taskId,
  editable,
  onChange,
  onAction
}: Readonly<{
  taskId: number;
  editable: boolean;
  onChange?: () => void;
  onAction?: (row: any) => void;
}>): ReactNode {
  const api = useApi();

  const [selected, setSelected] = useState<any>({});

  const query = useQuery({
    queryKey: ['fleet-task-checklist', taskId],
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_task_checklist_list), {
          params: { task: taskId }
        })
        .then((response) => response.data ?? [])
  });

  const refresh = useCallback(() => {
    query.refetch();
    onChange?.();
  }, [query, onChange]);

  const saveResult = useCallback(
    (row: any, data: Record<string, any>, then?: () => void) => {
      api
        .patch(apiUrl(ApiEndpoints.fleet_task_checklist_list, row.pk), data)
        .then(() => {
          refresh();
          then?.();
        })
        .catch((error) => {
          showApiErrorMessage({
            error: error,
            title: t`Error`,
            message: t`The checklist item could not be saved`
          });
        });
    },
    [api, refresh]
  );

  const editRow = useEditApiFormModal({
    url: ApiEndpoints.fleet_task_checklist_list,
    pk: selected.pk,
    title: selected.text ?? t`Checklist Item`,
    fields: checklistResultFields(),
    preFormContent:
      selected.result == 'ISSUE' ? (
        <Alert color='orange'>
          {t`Select the fault found. You can then record an action for it (e.g. replace a component).`}
        </Alert>
      ) : undefined,
    onFormSuccess: () => refresh()
  });

  const openEdit = useCallback(
    (row: any) => {
      setSelected(row);
      editRow.open();
    },
    [editRow]
  );

  // An issue is saved first, then its details (fault code) are asked for
  const markIssue = useCallback(
    (row: any) => {
      saveResult(row, { result: 'ISSUE' }, () =>
        openEdit({ ...row, result: 'ISSUE' })
      );
    },
    [saveResult, openEdit]
  );

  if (query.isLoading) {
    return <Skeleton height={120} />;
  }

  const rows: any[] = query.data ?? [];

  if (rows.length == 0) {
    return (
      <Text size='sm' c='dimmed'>
        {t`No checklist for this task. Checklist items are set per device type.`}
      </Text>
    );
  }

  return (
    <>
      {editRow.modal}
      <Stack gap='xs'>
        {rows.map((row: any) => (
          <ChecklistRow
            key={`${row.pk}-${row.result}-${row.value}`}
            row={row}
            editable={editable}
            onResult={saveResult}
            onIssue={markIssue}
            onEdit={openEdit}
            onAction={onAction}
          />
        ))}
      </Stack>
    </>
  );
}
