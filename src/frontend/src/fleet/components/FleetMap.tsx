import { t } from '@lingui/core/macro';
import {
  Anchor,
  Group,
  Skeleton,
  Stack,
  Text,
  useMantineTheme
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import type { LatLngExpression, LatLngTuple } from 'leaflet';
import { Fragment, type ReactNode, useEffect, useMemo } from 'react';
import {
  Circle,
  CircleMarker,
  MapContainer,
  Polygon,
  Polyline,
  Popup,
  TileLayer,
  useMap
} from 'react-leaflet';
import { useNavigate } from 'react-router-dom';

import 'leaflet/dist/leaflet.css';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { FleetStatus, FleetStatusBadge, fleetStatusColor } from './FleetBadges';

/** A deployed device, as returned by the deployment map endpoint */
export type FleetMapPoint = {
  pk: number;
  reference: string;
  site_name?: string | null;
  device_serial?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  nominal_latitude?: number | null;
  nominal_longitude?: number | null;
  last_latitude?: number | null;
  last_longitude?: number | null;
  distance_from_nominal_m?: number | null;
  radius_m?: number | null;
  polygon?: number[][] | null;
  health?: number;
  last_contact?: string | null;
  open_alert_count?: number;
};

// Default view (mainland Portugal) when there is nothing to show
const DEFAULT_CENTER: LatLngTuple = [39.5, -8.0];
const DEFAULT_ZOOM = 6;

function hasPosition(point: FleetMapPoint): boolean {
  return point.latitude != null && point.longitude != null;
}

/**
 * Fit the map to the given points (once per change of the point set)
 */
function FitBounds({ points }: Readonly<{ points: LatLngTuple[] }>) {
  const map = useMap();
  const key = JSON.stringify(points);

  useEffect(() => {
    if (points.length == 1) {
      map.setView(points[0], 13);
    } else if (points.length > 1) {
      map.fitBounds(points, { padding: [30, 30], maxZoom: 14 });
    }
  }, [map, key]);

  return null;
}

/**
 * Map of deployed devices.
 *
 * - Pins are coloured by health (at the last position, else the nominal one)
 * - The geofence is shown as a circle (radius) or a polygon
 * - When the last position differs from the nominal one, a line joins them
 * - An optional track (position history) is drawn as a line
 */
export function FleetMap({
  deployments,
  onSelect,
  track,
  height = 400,
  showGeofence = true
}: Readonly<{
  deployments: FleetMapPoint[];
  onSelect?: (point: FleetMapPoint) => void;
  track?: LatLngTuple[];
  height?: number | string;
  showGeofence?: boolean;
}>): ReactNode {
  const navigate = useNavigate();
  const theme = useMantineTheme();

  const points = useMemo(() => deployments.filter(hasPosition), [deployments]);

  const bounds: LatLngTuple[] = useMemo(() => {
    const result: LatLngTuple[] = points.map((p) => [
      p.latitude as number,
      p.longitude as number
    ]);

    return result.concat(track ?? []);
  }, [points, track]);

  return (
    <MapContainer
      center={DEFAULT_CENTER}
      zoom={DEFAULT_ZOOM}
      style={{ height: height, width: '100%', zIndex: 0 }}
      scrollWheelZoom={true}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url='https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
      />
      <FitBounds points={bounds} />
      {track && track.length > 1 && (
        <Polyline
          positions={track}
          pathOptions={{ color: '#1c7ed6', weight: 2, opacity: 0.7 }}
        />
      )}
      {points.map((point) => {
        // Pin colour: the colour of the health status (as its badge)
        const color =
          theme.colors[
            fleetStatusColor(FleetStatus.health, point.health)
          ]?.[6] ?? theme.colors.gray[6];
        const position: LatLngTuple = [
          point.latitude as number,
          point.longitude as number
        ];
        const nominal: LatLngTuple | null =
          point.nominal_latitude != null && point.nominal_longitude != null
            ? [point.nominal_latitude, point.nominal_longitude]
            : null;
        const moved =
          nominal != null &&
          point.last_latitude != null &&
          (nominal[0] != position[0] || nominal[1] != position[1]);

        return (
          <Fragment key={point.pk}>
            {showGeofence && point.polygon && point.polygon.length > 2 && (
              <Polygon
                positions={point.polygon as LatLngExpression[]}
                pathOptions={{ color: color, weight: 1, fillOpacity: 0.08 }}
              />
            )}
            {showGeofence && !point.polygon && nominal && point.radius_m && (
              <Circle
                center={nominal}
                radius={point.radius_m}
                pathOptions={{ color: color, weight: 1, fillOpacity: 0.08 }}
              />
            )}
            {moved && nominal && (
              <Polyline
                positions={[nominal, position]}
                pathOptions={{ color: color, weight: 2, dashArray: '4 4' }}
              />
            )}
            <CircleMarker
              center={position}
              radius={8}
              pathOptions={{
                color: '#ffffff',
                weight: 2,
                fillColor: color,
                fillOpacity: 1
              }}
              eventHandlers={{
                click: () => onSelect?.(point)
              }}
            >
              <Popup>
                <Stack gap={4}>
                  <Anchor
                    size='sm'
                    fw={700}
                    onClick={() =>
                      navigate(getDetailUrl(ModelType.deployment, point.pk))
                    }
                  >
                    {point.site_name ?? point.reference}
                  </Anchor>
                  <Text size='xs'>
                    {point.reference}
                    {point.device_serial ? ` · #${point.device_serial}` : ''}
                  </Text>
                  <Group gap={4}>
                    <FleetStatusBadge
                      type={FleetStatus.health}
                      status={point.health}
                      size='xs'
                    />
                    {!!point.open_alert_count && (
                      <Text size='xs' c='red'>
                        {t`Open alerts`}: {point.open_alert_count}
                      </Text>
                    )}
                  </Group>
                  <Text size='xs' c='dimmed'>
                    {t`Last data`}:{' '}
                    {point.last_contact
                      ? formatDate(point.last_contact, { showTime: true })
                      : t`Never`}
                  </Text>
                  {point.distance_from_nominal_m != null && (
                    <Text size='xs' c='dimmed'>
                      {t`From nominal`}:{' '}
                      {Math.round(point.distance_from_nominal_m)} m
                      {point.radius_m ? ` / ${point.radius_m} m` : ''}
                    </Text>
                  )}
                </Stack>
              </Popup>
            </CircleMarker>
          </Fragment>
        );
      })}
    </MapContainer>
  );
}

/**
 * Fleet map with data from the API.
 *
 * Shows the deployed devices (filtered by params), and the position track
 * when a single deployment is given.
 */
export function FleetMapView({
  params = {},
  deploymentId,
  height = 400
}: Readonly<{
  params?: Record<string, any>;
  deploymentId?: number;
  height?: number | string;
}>): ReactNode {
  const api = useApi();

  const queryParams = useMemo(
    () =>
      deploymentId ? { ...params, deployment: deploymentId } : { ...params },
    [params, deploymentId]
  );

  const mapQuery = useQuery({
    queryKey: ['fleet-map', queryParams],
    refetchOnMount: true,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_deployment_map), {
          params: queryParams
        })
        .then((res) => res.data ?? [])
  });

  const trackQuery = useQuery({
    queryKey: ['fleet-track', deploymentId],
    enabled: !!deploymentId,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.fleet_deployment_track, deploymentId))
        .then((res) => res.data ?? [])
  });

  const track: LatLngTuple[] = useMemo(
    () =>
      (trackQuery.data ?? []).map((fix: any) => [
        Number(fix.latitude),
        Number(fix.longitude)
      ]),
    [trackQuery.data]
  );

  if (mapQuery.isLoading) {
    return <Skeleton height={height} />;
  }

  const deployments: FleetMapPoint[] = mapQuery.data ?? [];

  // Devices without a position (none set, none reported) are not drawn
  const missing = deployments.filter((point) => !hasPosition(point)).length;

  return (
    <Stack gap='xs'>
      <FleetMap
        deployments={deployments}
        track={deploymentId ? track : undefined}
        height={height}
      />
      {missing > 0 && (
        <Text size='sm' c='dimmed'>
          {deploymentId
            ? t`This device has no position yet. Use "Set Position" to add it.`
            : t`Devices without a position (not shown on the map): ${missing}`}
        </Text>
      )}
    </Stack>
  );
}
