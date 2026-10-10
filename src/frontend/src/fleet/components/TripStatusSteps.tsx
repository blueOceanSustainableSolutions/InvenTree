import { t } from '@lingui/core/macro';
import { Alert, Stepper } from '@mantine/core';
import { IconCircleX } from '@tabler/icons-react';
import type { ReactNode } from 'react';

import { ModelType } from '@lib/enums/ModelType';
import useStatusCodes from '../../hooks/UseStatusCodes';

/**
 * Status steps of a field trip:
 * Planning -> Kit ready -> In progress -> Reconciling -> Closed
 */
export function TripStatusSteps({
  status
}: Readonly<{ status: number }>): ReactNode {
  const tripStatus = useStatusCodes({ modelType: ModelType.fieldtrip });

  if (status == tripStatus.CANCELLED) {
    return (
      <Alert color='red' icon={<IconCircleX />}>
        {t`This trip was cancelled`}
      </Alert>
    );
  }

  // Trip status codes, in the order of the steps
  const steps = [
    tripStatus.PLANNING,
    tripStatus.KIT_READY,
    tripStatus.IN_PROGRESS,
    tripStatus.RECONCILING,
    tripStatus.CLOSED
  ];

  const index = steps.indexOf(status);

  // A closed trip shows every step as done
  const active =
    status == tripStatus.CLOSED ? steps.length : Math.max(index, 0);

  return (
    <Stepper active={active} size='sm' allowNextStepsSelect={false}>
      <Stepper.Step label={t`Planning`} description={t`Tasks and kit list`} />
      <Stepper.Step label={t`Kit ready`} description={t`Stock in the kit`} />
      <Stepper.Step label={t`In progress`} description={t`In the field`} />
      <Stepper.Step label={t`Reconciling`} description={t`Return the kit`} />
      <Stepper.Step label={t`Closed`} />
    </Stepper>
  );
}
