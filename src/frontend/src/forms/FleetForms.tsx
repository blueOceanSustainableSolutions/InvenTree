import { t } from '@lingui/core/macro';
import { Table, Text } from '@mantine/core';
import { IconUsersGroup } from '@tabler/icons-react';
import { type ReactNode, useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import type { ApiFormFieldSet, ApiFormFieldType } from '@lib/types/Forms';
import RemoveRowButton from '../components/buttons/RemoveRowButton';
import { StandaloneField } from '../components/forms/StandaloneField';
import type { TableFieldRowProps } from '../components/forms/fields/TableField';
import {
  type TeamErrorStore,
  TeamSelect
} from '../fleet/components/TeamSelect';
import { RenderPartColumn } from '../tables/ColumnRenderers';

/**
 * Fields for creating or editing a fleet site
 */
export function useSiteFields(): ApiFormFieldSet {
  return useMemo(
    () => ({
      reference: {},
      name: {},
      latitude: {},
      longitude: {},
      depth_m: {},
      coverage: {},
      client: {
        filters: { is_customer: true }
      },
      project: {},
      pm_interval_override_days: {},
      geofence_radius_m: {},
      active: {}
    }),
    []
  );
}

/**
 * Fields for creating or editing a fleet device type
 */
export function useDeviceTypeFields({
  partId
}: {
  partId?: number;
}): ApiFormFieldSet {
  return useMemo(
    () => ({
      part: {
        filters: { assembly: true, trackable: true },
        value: partId,
        disabled: !!partId
      },
      pm_interval_days: {},
      verify_window_minutes: {},
      active: {}
    }),
    [partId]
  );
}

/**
 * Fields for a data stream template of a device type
 */
export function streamTemplateFields(deviceTypeId?: number): ApiFormFieldSet {
  return {
    device_type: {
      hidden: true,
      value: deviceTypeId
    },
    key: {},
    name: {},
    essential: {},
    expected_interval_minutes: {},
    grace_minutes: {}
  };
}

/**
 * Fields for planning or editing a deployment
 */
export function useDeploymentFields({
  create
}: {
  create: boolean;
}): ApiFormFieldSet {
  return useMemo(() => {
    const fields: ApiFormFieldSet = {
      reference: {},
      device_type: {
        filters: { active: true },
        disabled: !create
      },
      site: {
        filters: { active: true }
      },
      deployment_type: {},
      replaces: {
        filters: { active: true },
        description: t`For a replacement: the deployment which it ends`
      },
      target_date: {},
      client: {
        filters: { is_customer: true },
        description: t`Leave blank to use the site client`
      },
      coverage: {}
    };

    if (!create) {
      fields.next_pm_date = {};
      fields.next_pm_manual = {};
      fields.latitude = {};
      fields.longitude = {};
      fields.depth_m = {};
      fields.geofence_radius_m = {};
    }

    return fields;
  }, [create]);
}

/**
 * Fields for planning where and when a pipeline deployment goes
 * (the "set date or site" action of the pipeline)
 */
export function planDeploymentFields(): ApiFormFieldSet {
  return {
    site: {
      filters: { active: true }
    },
    target_date: {}
  };
}

/**
 * Fields for deploying a device (the site is optional)
 */
export function deployFields(): ApiFormFieldSet {
  return {
    latitude: {},
    longitude: {},
    depth_m: {},
    deployed_at: {}
  };
}

/**
 * Fields for setting the position of a device
 */
export function setPositionFields(): ApiFormFieldSet {
  return {
    latitude: {},
    longitude: {},
    depth_m: {},
    geofence_radius_m: {}
  };
}

/**
 * Fields for setting the internal state of a device. Problem acknowledged and
 * docked are only offered for a device in the water.
 */
export function setDeviceStateFields(deployed: boolean): ApiFormFieldSet {
  const choices = [
    { value: '', display_name: t`None (clear the state)` },
    ...(deployed
      ? [
          {
            value: 'PROBLEM_ACKNOWLEDGED',
            display_name: t`Problem Acknowledged`
          },
          { value: 'DOCKED', display_name: t`Docked` }
        ]
      : []),
    { value: 'DECOMMISSIONED', display_name: t`Decommissioned` }
  ];

  return {
    state: {
      choices: choices
    },
    note: {}
  };
}

/**
 * Fields for recovering a deployed device
 */
export function recoverFields(): ApiFormFieldSet {
  return {
    location: {
      filters: { structural: false }
    },
    notes: {}
  };
}

/**
 * Fields for assigning an existing unit to a deployment
 */
export function assignDeviceFields(deployment: any): ApiFormFieldSet {
  return {
    stock_item: {
      filters: {
        part: deployment?.device_type_detail?.part,
        include_variants: true,
        in_stock: true,
        serialized: true,
        part_detail: true,
        location_detail: true
      }
    }
  };
}

/**
 * Fields for editing the data stream of a deployment
 */
export function dataStreamFields(deploymentId?: number): ApiFormFieldSet {
  return {
    deployment: {
      hidden: true,
      value: deploymentId
    },
    key: {},
    name: {},
    essential: {},
    enabled: {},
    expected_interval_minutes: {},
    grace_minutes: {}
  };
}

/**
 * Fields for resolving a fleet alert by hand
 */
export function resolveAlertFields(): ApiFormFieldSet {
  return {
    note: {}
  };
}

/**
 * Fields for the platform link (data platform id) of a device
 */
export function deviceLinkFields(stockItemId?: number): ApiFormFieldSet {
  return {
    stock_item: {
      hidden: true,
      value: stockItemId
    },
    platform_id: {},
    firmware_version: {},
    comment: {}
  };
}

/**
 * Render a fault code in a related field
 */
export function renderFaultCode({ instance }: { instance: any }): ReactNode {
  if (!instance) {
    return null;
  }

  return (
    <Text size='sm'>
      {instance.name}{' '}
      <Text span size='xs' c='dimmed'>
        {instance.code}
      </Text>
    </Text>
  );
}

/**
 * Render a checklist item (of a task) in a related field
 */
export function renderChecklistResult({
  instance
}: {
  instance: any;
}): ReactNode {
  return instance ? <Text size='sm'>{instance.text}</Text> : null;
}

/**
 * Render a data stream (of a deployment) in a related field
 */
export function renderDataStream({ instance }: { instance: any }): ReactNode {
  return instance ? (
    <Text size='sm'>{instance.name || instance.key}</Text>
  ) : null;
}

/**
 * Fields for a checklist template item of a device type
 */
export function checklistItemFields(deviceTypeId?: number): ApiFormFieldSet {
  return {
    device_type: {
      hidden: true,
      value: deviceTypeId
    },
    sequence: {},
    text: {},
    kind: {},
    unit: {},
    required: {},
    task_type: {}
  };
}

/**
 * Fields for a kit template line of a device type
 */
export function kitLineFields(deviceTypeId?: number): ApiFormFieldSet {
  return {
    device_type: {
      hidden: true,
      value: deviceTypeId
    },
    part: {
      filters: { active: true }
    },
    quantity: {},
    mode: {}
  };
}

/**
 * Fields for creating or editing a maintenance task
 */
export function useTaskFields({
  create,
  deviceId,
  deploymentId
}: {
  create: boolean;
  deviceId?: number;
  deploymentId?: number;
}): ApiFormFieldSet {
  return useMemo(() => {
    const fields: ApiFormFieldSet = {
      task_type: {},
      description: {},
      device: {
        filters: {
          serialized: true,
          part_detail: true,
          location_detail: true
        },
        disabled: !create || !!deviceId
      },
      deployment: {
        filters: deviceId ? { device: deviceId } : { active: true },
        disabled: !create || !!deploymentId,
        description: t`Leave blank to use the active deployment of the device`
      },
      site: {
        filters: { active: true },
        description: t`Leave blank to use the site of the deployment`
      },
      due_date: {},
      scheduled_date: {
        description: t`A scheduled date moves a proposed task to scheduled`
      }
    };

    if (!create) {
      fields.as_found = {};
      fields.summary = {};
      fields.labour_minutes = {};
    }

    return fields;
  }, [create, deviceId, deploymentId]);
}

/**
 * Fields for filling in a checklist item of a task
 */
export function checklistResultFields(): ApiFormFieldSet {
  return {
    result: {},
    value: {},
    note: {},
    fault_code: {
      filters: { active: true },
      modelRenderer: renderFaultCode
    }
  };
}

/**
 * Extra fields of a component action during a task
 */
export function taskComponentFields(taskId?: number): ApiFormFieldSet {
  return {
    fault_code: {
      filters: { active: true },
      modelRenderer: renderFaultCode
    },
    checklist_result: {
      filters: { task: taskId },
      modelRenderer: renderChecklistResult
    }
  };
}

/**
 * Extra field of a removal during a task: the stream the device no longer reports
 */
export function taskRemovalFields(deploymentId?: number): ApiFormFieldSet {
  return {
    disable_stream: {
      hidden: !deploymentId,
      filters: { deployment: deploymentId, enabled: true },
      modelRenderer: renderDataStream
    }
  };
}

/**
 * Fields for using a consumable during a task
 */
export function consumeFields(kitLocationId?: number): ApiFormFieldSet {
  return {
    stock_item: {
      filters: {
        in_stock: true,
        serialized: false,
        available: true,
        part_detail: true,
        location_detail: true,
        ...(kitLocationId ? { location: kitLocationId, cascade: true } : {})
      }
    },
    quantity: {},
    note: {}
  };
}

/**
 * Fields for moving the nominal position of a device during a task
 */
export function repositionFields(): ApiFormFieldSet {
  return {
    latitude: {},
    longitude: {},
    note: {}
  };
}

/**
 * Fields for recording an action without a stock change
 */
export function recordActionFields(taskId?: number): ApiFormFieldSet {
  return {
    action: {},
    note: {},
    fault_code: {
      filters: { active: true },
      modelRenderer: renderFaultCode
    },
    checklist_result: {
      filters: { task: taskId },
      modelRenderer: renderChecklistResult
    },
    firmware_version: {}
  };
}

/**
 * Fields for completing a task
 */
export function completeTaskFields(needsOverride: boolean): ApiFormFieldSet {
  return {
    labour_minutes: {},
    summary: {},
    override_reason: {
      hidden: !needsOverride
    }
  };
}

/**
 * Fields for cancelling a task
 */
export function cancelTaskFields(): ApiFormFieldSet {
  return {
    reason: {}
  };
}

/**
 * Fields for creating a corrective task from an alert
 */
export function alertCreateTaskFields(): ApiFormFieldSet {
  return {
    scheduled_date: {},
    description: {},
    trip: {
      filters: { open: true }
    }
  };
}

/**
 * Fields for creating or editing a field trip.
 *
 * The team (many users) has no form field type, so it is picked with a
 * TeamSelect below the responsible owner (a user or a group); the caller
 * keeps the value (onTeamChange) and adds it to the submitted data
 * (processFormData), and puts the backend errors of `team` into `teamErrors`
 * (onFormError).
 */
export function useTripFields({
  initialTeam,
  onTeamChange,
  teamErrors
}: {
  initialTeam: number[];
  onTeamChange: (value: number[]) => void;
  teamErrors?: TeamErrorStore;
}): ApiFormFieldSet {
  return useMemo(
    () => ({
      title: {},
      start_date: {},
      end_date: {},
      vessel: {},
      responsible: {
        icon: <IconUsersGroup />,
        filters: { is_active: true },
        postFieldContent: (
          <TeamSelect
            defaultValue={initialTeam}
            onChange={onTeamChange}
            errors={teamErrors}
          />
        )
      }
    }),
    [initialTeam, onTeamChange, teamErrors]
  );
}

/**
 * Fields for a kit line added by hand to a trip
 */
export function tripKitLineFields(tripId?: number): ApiFormFieldSet {
  return {
    trip: {
      hidden: true,
      value: tripId
    },
    part: {
      filters: { active: true }
    },
    quantity_planned: {},
    note: {}
  };
}

/**
 * One row of the prepare kit form: a stock item (and quantity) to move into
 * the trip kit. A row made from a kit line shows its part and filters the
 * stock by it; a DEVICE line fixes the stock item (the device to deploy).
 *
 * The stock item is a normal related field, so the barcode button
 * (scan-to-pick) is shown when barcodes in form fields are enabled.
 */
export type PrepareKitRowItem = {
  part?: number;
  part_detail?: any;
  missing?: number;
  fixed?: boolean;
  stock_item?: number;
  quantity?: number;
};

function PrepareKitRow({
  props
}: Readonly<{
  props: TableFieldRowProps;
}>): ReactNode {
  const item: PrepareKitRowItem = props.item;

  const stockField: ApiFormFieldType = useMemo(
    () => ({
      field_type: 'related field',
      api_url: apiUrl(ApiEndpoints.stock_item_list),
      model: ModelType.stockitem,
      required: true,
      disabled: !!item.fixed,
      value: item.stock_item,
      filters: {
        part: item.part,
        include_variants: true,
        in_stock: true,
        available: true,
        part_detail: true,
        location_detail: true
      },
      onValueChange: (value: any) => {
        props.changeFn(props.idx, 'stock_item', value);
      }
    }),
    [item.part, item.fixed, item.stock_item, props]
  );

  const quantityField: ApiFormFieldType = useMemo(
    () => ({
      field_type: 'number',
      required: false,
      value: item.quantity,
      placeholder: t`Whole stock item`,
      onValueChange: (value: any) => {
        props.changeFn(props.idx, 'quantity', value);
      }
    }),
    [item.quantity, props]
  );

  return (
    <Table.Tr key={`prepare-kit-row-${props.idx}`}>
      <Table.Td>
        {item.part_detail ? (
          <RenderPartColumn part={item.part_detail} />
        ) : (
          <Text size='sm' c='dimmed'>{t`Any part`}</Text>
        )}
      </Table.Td>
      <Table.Td>
        <Text size='sm'>{item.missing ?? '-'}</Text>
      </Table.Td>
      <Table.Td>
        <StandaloneField
          fieldName='stock_item'
          fieldDefinition={stockField}
          error={props.rowErrors?.stock_item?.message}
        />
      </Table.Td>
      <Table.Td>
        <StandaloneField
          fieldName='quantity'
          fieldDefinition={quantityField}
          error={props.rowErrors?.quantity?.message}
        />
      </Table.Td>
      <Table.Td>
        <RemoveRowButton onClick={() => props.removeFn(props.idx)} />
      </Table.Td>
    </Table.Tr>
  );
}

/**
 * Fields for taking stock into a trip kit (prepare kit): a table with one
 * row per stock item, as the core allocation forms. Rows are pre-filled from
 * the selected kit lines (see usePrepareKitForm); more rows can be added.
 */
export function prepareKitFields(): ApiFormFieldSet {
  return {
    items: {
      field_type: 'table',
      value: [],
      headers: [
        { title: t`Part`, style: { minWidth: '175px' } },
        { title: t`Missing`, style: { minWidth: '75px' } },
        { title: t`Stock Item`, style: { width: '100%' } },
        { title: t`Quantity`, style: { minWidth: '150px' } },
        { title: '', style: { width: '50px' } }
      ],
      modelRenderer: (row: TableFieldRowProps) => (
        <PrepareKitRow key={row.idx} props={row} />
      ),
      addRow: (): PrepareKitRowItem => ({
        stock_item: undefined,
        quantity: undefined
      })
    }
  };
}

/**
 * Turn the prepare kit form data into the request body:
 * {items: [{stock_item, quantity}]}, without a quantity when it is blank
 * (the whole stock item is taken). Rows are kept in order, so that the
 * row errors of the response match the rows of the form.
 */
export function prepareKitData(data: any): any {
  const items = (data.items ?? []).map((row: PrepareKitRowItem) => {
    const item: Record<string, any> = { stock_item: row.stock_item ?? null };

    if (row.quantity != null && (row.quantity as any) !== '') {
      item.quantity = row.quantity;
    }

    return item;
  });

  return { items: items };
}

/**
 * Fields for cancelling a field trip
 */
export function cancelTripFields(): ApiFormFieldSet {
  return {
    reason: {}
  };
}

/**
 * Fields for deploying the device of a deployment or swap task
 */
export function taskDeployFields(): ApiFormFieldSet {
  return {
    latitude: {},
    longitude: {},
    depth_m: {},
    deployed_at: {},
    note: {}
  };
}
