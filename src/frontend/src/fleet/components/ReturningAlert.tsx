import { t } from '@lingui/core/macro';
import { Alert, Anchor, Text } from '@mantine/core';
import { IconTruckReturn } from '@tabler/icons-react';
import type { ReactNode } from 'react';

/**
 * A device on its way back to the office: the return order which brings it
 * (created when it was recovered). It can be planned for its next deployment
 * now, and deployed once the office has received it.
 */
export function ReturningAlert({
  deployment,
  href,
  onClick
}: Readonly<{
  deployment: any;
  href?: string;
  onClick?: (event: any) => void;
}>): ReactNode {
  if (!deployment?.return_order) {
    return null;
  }

  return (
    <Alert
      color='yellow'
      icon={<IconTruckReturn />}
      title={t`On its way back to the office`}
    >
      <Text size='sm'>
        {t`Return order`}{' '}
        <Anchor size='sm' href={href} onClick={onClick}>
          {deployment.return_order_reference ?? deployment.return_order}
        </Anchor>
        {'. '}
        {t`The device can be planned for its next deployment now, and deployed once the office has received it.`}
      </Text>
    </Alert>
  );
}
