import { t } from '@lingui/core/macro';
import { Tabs, Text } from '@mantine/core';
import { useLocalStorage } from '@mantine/hooks';
import {
  IconCalendarEvent,
  IconCalendarQuestion,
  IconCircleCheck
} from '@tabler/icons-react';
import type { ReactNode } from 'react';

import { ModelType } from '@lib/enums/ModelType';
import { PipelineKpis } from '../../fleet/components/PipelineKpis';
import { DeploymentTable } from '../../fleet/tables/DeploymentTable';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { PortalPage } from '../components/PortalPage';

/**
 * Deployment pipeline (manager), grouped:
 * - Unscheduled: no target date yet; set the site and date from the row;
 * - Scheduled: by target date, with the readiness risks;
 * - Ready: built and waiting; tick them to create a field trip or add them
 *   to one.
 *
 * Rows also offer "Create Build Order" and "Assign Device" (an existing unit);
 * "Plan Deployment" creates a new one. Nothing needs a site or a position.
 */
export default function Pipeline(): ReactNode {
  const [group, setGroup] = useLocalStorage<string>({
    key: 'fleet-portal-pipeline-group',
    defaultValue: 'unscheduled'
  });
  const deploymentStatus = useStatusCodes({ modelType: ModelType.deployment });

  return (
    <PortalPage title={t`Deployment Pipeline`}>
      <PipelineKpis />
      <Tabs
        value={group}
        onChange={(value) => setGroup(value ?? 'unscheduled')}
        keepMounted={false}
      >
        <Tabs.List>
          <Tabs.Tab
            value='unscheduled'
            leftSection={<IconCalendarQuestion size={18} />}
          >
            {t`Unscheduled`}
          </Tabs.Tab>
          <Tabs.Tab
            value='scheduled'
            leftSection={<IconCalendarEvent size={18} />}
          >
            {t`Scheduled`}
          </Tabs.Tab>
          <Tabs.Tab value='ready' leftSection={<IconCircleCheck size={18} />}>
            {t`Ready`}
          </Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value='unscheduled' pt='xs'>
          <Text size='sm' c='dimmed' pb='xs'>
            {t`Deployments without a target date. Use "Set Site and Date" on a row to plan them.`}
          </Text>
          <DeploymentTable
            mode='pipeline'
            params={{ unscheduled: true }}
            tableName='fleet-portal-pipeline-unscheduled'
          />
        </Tabs.Panel>
        <Tabs.Panel value='scheduled' pt='xs'>
          <Text size='sm' c='dimmed' pb='xs'>
            {t`Deployments with a target date, soonest first, with their readiness risks.`}
          </Text>
          <DeploymentTable
            mode='pipeline'
            params={{ unscheduled: false, ordering: 'target_date' }}
            tableName='fleet-portal-pipeline-scheduled'
          />
        </Tabs.Panel>
        <Tabs.Panel value='ready' pt='xs'>
          <Text size='sm' c='dimmed' pb='xs'>
            {t`Built devices waiting for deployment. Tick them to create a field trip or to add them to one.`}
          </Text>
          <DeploymentTable
            mode='pipeline'
            params={{ status: deploymentStatus.READY }}
            selection='trip'
            tableName='fleet-portal-pipeline-ready'
          />
        </Tabs.Panel>
      </Tabs>
    </PortalPage>
  );
}
