import { t } from '@lingui/core/macro';
import { Alert } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { siteHasLocation, siteMoveFields } from '../../forms/FleetForms';
import { useCreateApiFormModal } from '../../hooks/UseForm';

/**
 * "Move Site": the device was moved (or the site location is set for the
 * first time). The old location, when, and why are kept in the location
 * history of the site. Used by the site and deployment pages, in the main UI
 * and in the Fleet Portal.
 */
export function useSiteMoveForm({
  site,
  onSuccess
}: {
  site: any;
  onSuccess?: () => void;
}) {
  const located = siteHasLocation(site);

  return useCreateApiFormModal({
    url: apiUrl(ApiEndpoints.fleet_site_move, site?.pk),
    title: located ? t`Move Site` : t`Set Site Location`,
    fields: siteMoveFields(),
    initialData: {
      latitude: site?.latitude,
      longitude: site?.longitude,
      depth_m: site?.depth_m
    },
    preFormContent: (
      <Alert color='blue' icon={<IconInfoCircle />}>
        {located
          ? t`The site is where its device should be. Moving it keeps the old location and the reason in the location history, and the geofence moves with it. Use it when the device was moved on purpose.`
          : t`The site is where its device should be. The location is kept in the location history, with the reason.`}
      </Alert>
    ),
    successMessage: located ? t`Site moved` : t`Site location set`,
    onFormSuccess: () => onSuccess?.()
  });
}
