import { t } from '@lingui/core/macro';
import { Badge, Group, type MantineSize, Tooltip } from '@mantine/core';
import type { ReactNode } from 'react';

import type { TableFilterChoice } from '@lib/types/Filters';
import {
  StatusRenderer,
  getStatusCodes
} from '../../components/render/StatusRenderer';
import { statusColorMap } from '../../defaults/backendMappings';

/**
 * The fleet status code classes without a model type of their own. They are
 * served by the generic status API and stored under their class name.
 */
export const FleetStatus = {
  health: 'HealthStatus',
  severity: 'AlertSeverity',
  stream: 'DataStreamStatus'
} as const;

export type FleetStatusType = (typeof FleetStatus)[keyof typeof FleetStatus];

/**
 * Badge for a fleet status code (health, alert severity, data stream state),
 * rendered by the core status renderer
 */
export function FleetStatusBadge({
  type,
  status,
  size = 'sm'
}: Readonly<{
  type: FleetStatusType;
  status?: number | null;
  size?: MantineSize;
}>): ReactNode {
  if (status === undefined || status === null) {
    return null;
  }

  return StatusRenderer({ status: status, type: type, options: { size } });
}

/** Filter choices for a fleet status code class */
export function fleetStatusChoices(
  type: FleetStatusType
): () => TableFilterChoice[] {
  return () =>
    Object.values(getStatusCodes(type)?.values ?? {}).map((entry) => ({
      value: entry.key.toString(),
      label: entry.label ?? entry.key.toString()
    }));
}

/** Mantine colour name of a fleet status code (as the status badge shows it) */
export function fleetStatusColor(
  type: FleetStatusType,
  status?: number | null
): string {
  const entry = Object.values(getStatusCodes(type)?.values ?? {}).find(
    (value) => value.key == status
  );

  return statusColorMap[entry?.color ?? 'default'] ?? statusColorMap.default;
}

/** Display name of a user (from a core user detail object) */
export function userDisplayName(user?: any): string {
  if (!user) {
    return '';
  }

  const name = [user.first_name, user.last_name].filter(Boolean).join(' ');

  return name || user.username || '';
}

/** Display names of a list of users, comma separated */
export function userListNames(users?: any[] | null): string {
  return (users ?? []).map(userDisplayName).filter(Boolean).join(', ');
}

/** Label and colour for each site / deployment coverage value */
function coverageInfo(coverage: string): { label: string; color: string } {
  switch (coverage) {
    case 'NO_SERVICE':
      return { label: t`Monitored only`, color: 'gray' };
    case 'THIRD_PARTY':
      return { label: t`Third party`, color: 'violet' };
    default:
      return { label: t`Full service`, color: 'teal' };
  }
}

/**
 * Badge showing the service coverage of a site or deployment
 */
export function CoverageBadge({
  coverage,
  size = 'sm'
}: Readonly<{ coverage?: string; size?: string }>): ReactNode {
  if (!coverage) {
    return null;
  }

  const info = coverageInfo(coverage);

  return (
    <Badge color={info.color} variant='light' size={size}>
      {info.label}
    </Badge>
  );
}

/** Short label for each readiness risk code */
function riskLabel(code: string): string {
  switch (code) {
    case 'BUILD_LATE':
      return t`Build late`;
    case 'NOT_READY':
      return t`Not ready`;
    case 'PARTS_SHORT':
      return t`Parts short`;
    case 'NO_DEPLOY_DATE':
      return t`No date`;
    default:
      return code;
  }
}

/**
 * Badges for the readiness risks of a pipeline deployment
 * (from the "risks" output option of the deployment API)
 */
export function RiskBadges({
  risks
}: Readonly<{ risks?: { code: string; severity: number; message: string }[] }>):
  | ReactNode
  | undefined {
  if (!risks || risks.length == 0) {
    return null;
  }

  return (
    <Group gap={4}>
      {risks.map((risk) => (
        <Tooltip key={risk.code} label={risk.message}>
          <Badge
            color={fleetStatusColor(FleetStatus.severity, risk.severity)}
            size='sm'
            variant='light'
          >
            {riskLabel(risk.code)}
          </Badge>
        </Tooltip>
      ))}
    </Group>
  );
}

/** Label for each alert type */
export function alertTypeLabel(alertType: string): string {
  switch (alertType) {
    case 'STREAM_LATE':
      return t`Stream late`;
    case 'STREAM_MISSING':
      return t`Stream missing`;
    case 'NO_CONTACT':
      return t`No contact`;
    case 'GEOFENCE_BREACH':
      return t`Geofence breach`;
    case 'PM_DUE':
      return t`Maintenance due`;
    case 'PM_OVERDUE':
      return t`Maintenance overdue`;
    default:
      return riskLabel(alertType);
  }
}

/** Choices for the alert type filter of alert tables */
export function alertTypeChoices() {
  return [
    'STREAM_LATE',
    'STREAM_MISSING',
    'NO_CONTACT',
    'GEOFENCE_BREACH',
    'PM_DUE',
    'PM_OVERDUE',
    'NOT_READY',
    'BUILD_LATE',
    'NO_DEPLOY_DATE',
    'PARTS_SHORT'
  ].map((value) => ({ value: value, label: alertTypeLabel(value) }));
}

/** Label for each maintenance task type */
export function taskTypeLabel(taskType: string): string {
  switch (taskType) {
    case 'PREVENTIVE':
      return t`Preventive`;
    case 'CORRECTIVE':
      return t`Corrective`;
    case 'DEPLOYMENT':
      return t`Deployment`;
    case 'RECOVERY':
      return t`Recovery`;
    case 'SWAP':
      return t`Swap`;
    case 'INSPECTION':
      return t`Inspection`;
    default:
      return taskType;
  }
}

/** Choices for the task type filter of fleet tables */
export function taskTypeChoices() {
  return [
    'PREVENTIVE',
    'CORRECTIVE',
    'DEPLOYMENT',
    'RECOVERY',
    'SWAP',
    'INSPECTION'
  ].map((value) => ({ value: value, label: taskTypeLabel(value) }));
}

/** Label for each maintenance action */
export function maintenanceActionLabel(action: string): string {
  switch (action) {
    case 'REPLACE':
      return t`Replace`;
    case 'REMOVE':
      return t`Remove`;
    case 'ADD':
      return t`Add`;
    case 'DESTROY':
      return t`Destroy`;
    case 'CONSUME':
      return t`Consume`;
    case 'REPAIR':
      return t`Repair`;
    case 'CLEAN':
      return t`Clean`;
    case 'FIRMWARE':
      return t`Firmware`;
    case 'REPOSITION':
      return t`Reposition`;
    default:
      return t`Other`;
  }
}

/** Label and colour for each checklist result */
export function checklistResultInfo(result: string): {
  label: string;
  color: string;
} {
  switch (result) {
    case 'OK':
      return { label: t`OK`, color: 'green' };
    case 'ISSUE':
      return { label: t`Issue`, color: 'red' };
    case 'NA':
      return { label: t`N/A`, color: 'gray' };
    default:
      return { label: t`Pending`, color: 'yellow' };
  }
}

/** Label, colour and explanation of each internal device state */
export function deviceStateInfo(state: string): {
  label: string;
  color: string;
  description: string;
} {
  switch (state) {
    case 'DECOMMISSIONED':
      return {
        label: t`Decommissioned`,
        color: 'dark',
        description: t`Will not become active again; never reopened by the stock sync`
      };
    case 'DOCKED':
      return {
        label: t`Docked`,
        color: 'grape',
        description: t`Back on land: not monitored, not planned, not on the map`
      };
    case 'MAINTENANCE_SCHEDULED':
      return {
        label: t`Maintenance Scheduled`,
        color: 'cyan',
        description: t`A task on this device is on a field trip`
      };
    case 'MAINTENANCE_OVERDUE':
      return {
        label: t`Maintenance Overdue`,
        color: 'orange',
        description: t`Routine maintenance is past due`
      };
    case 'PROBLEM_ACKNOWLEDGED':
      return {
        label: t`Problem Acknowledged`,
        color: 'yellow',
        description: t`The problem is known: notifications are muted until it clears`
      };
    case 'UNRESPONSIVE':
      return {
        label: t`Unresponsive`,
        color: 'red',
        description: t`No data received recently`
      };
    case 'ACTIVE':
      return {
        label: t`Active`,
        color: 'green',
        description: t`Deployed and sending data`
      };
    default:
      return { label: state, color: 'gray', description: '' };
  }
}

/**
 * Badge showing the internal state of a device (nothing outside of deployed,
 * except decommissioned)
 */
export function DeviceStateBadge({
  state,
  note,
  size = 'sm'
}: Readonly<{ state?: string | null; note?: string; size?: string }>):
  | ReactNode
  | undefined {
  if (!state) {
    return null;
  }

  const info = deviceStateInfo(state);
  const tooltip = note ? `${info.description} - ${note}` : info.description;

  return (
    <Tooltip label={tooltip} disabled={!tooltip} multiline maw={320}>
      <Badge color={info.color} variant='light' size={size}>
        {info.label}
      </Badge>
    </Tooltip>
  );
}

/** Choices for the device state filter */
export function deviceStateChoices() {
  return [
    'ACTIVE',
    'UNRESPONSIVE',
    'PROBLEM_ACKNOWLEDGED',
    'MAINTENANCE_OVERDUE',
    'MAINTENANCE_SCHEDULED',
    'DOCKED',
    'DECOMMISSIONED'
  ].map((value) => ({ value: value, label: deviceStateInfo(value).label }));
}
