import { t } from '@lingui/core/macro';
import { Stack } from '@mantine/core';
import { useMemo } from 'react';

import { PluginPanelKey } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import type { PanelType } from '@lib/types/Panel';
import PermissionDenied from '../../components/errors/PermissionDenied';
import { PageDetail } from '../../components/nav/PageDetail';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { fleetSections } from '../../fleet/FleetSections';
import { useUserState } from '../../states/UserState';

/**
 * Fleet index page: one panel per fleet section. The sections are shared
 * with the Fleet Portal (fleet/FleetSections.tsx), so both show the same.
 */
export default function FleetIndex() {
  const user = useUserState();

  const panels: PanelType[] = useMemo(
    () =>
      fleetSections().map((section) => ({
        name: section.name,
        label: section.label,
        icon: section.icon,
        content: section.content
      })),
    []
  );

  if (!user.isLoggedIn() || !user.hasViewRole(UserRoles.fleet)) {
    return <PermissionDenied />;
  }

  return (
    <Stack>
      <PageDetail title={t`Fleet`} actions={[]} />
      <PanelGroup
        pageKey='fleet-index'
        panels={panels}
        pluginPanelWithoutId
        pluginPanelKey={PluginPanelKey.fleet}
      />
    </Stack>
  );
}
