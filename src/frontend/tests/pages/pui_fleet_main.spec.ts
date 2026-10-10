import { type APIRequestContext, expect } from '@playwright/test';
import { createApi } from '../api.ts';
import { test } from '../baseFixtures.ts';
import { adminuser } from '../defaults.ts';
import {
  clearTableFilters,
  loadTab,
  navigate,
  setTableChoiceFilter,
  showTableView
} from '../helpers.ts';
import { doCachedLogin } from '../login.ts';

/*
 * Fleet pages of the main UI (/web/fleet/...): the index panels and the
 * detail pages (deployment, task, trip, site, device type) with their tabs.
 *
 * The demo dataset has no fleet data, so the detail tests create their own
 * through the API (a trackable part with one serialized unit, a device type,
 * a site, a planned deployment, a corrective task and a field trip). The
 * admin user is used: the "fleet" role is not assigned to the other demo
 * users.
 */

type FleetMainData = {
  uid: string;
  deviceType: number;
  site: { pk: number; reference: string; name: string };
  deployment: { pk: number; reference: string };
  task: { pk: number; reference: string };
  trip: { pk: number; reference: string; title: string };
};

/** POST and return the JSON body, failing with the response text */
async function post(api: APIRequestContext, url: string, data: any) {
  const response = await api.post(url, { data: data });
  expect(response.ok(), `POST ${url}: ${await response.text()}`).toBeTruthy();
  return response.json();
}

/** GET a list and return the JSON body */
async function list(api: APIRequestContext, url: string, params: any) {
  const response = await api.get(url, { params: params });
  expect(response.ok(), `GET ${url}: ${await response.text()}`).toBeTruthy();
  return response.json();
}

/** Create the fleet records opened by the detail page tests */
async function setupFleetMainData(): Promise<FleetMainData> {
  const api = await createApi({});
  const uid = `${Date.now()}`.slice(-8);
  const serial = `PWM${uid}`;

  const part = await post(api, 'part/', {
    name: `Fleet PW Main Unit ${uid}`,
    description: 'Fleet main UI test device',
    trackable: true,
    assembly: true,
    active: true
  });

  await post(api, 'stock/', {
    part: part.pk,
    quantity: 1,
    serial_numbers: serial
  });

  const items = await list(api, 'stock/', { part: part.pk, serial: serial });
  expect(items.length).toBe(1);
  const device = items[0];

  const deviceType = await post(api, 'fleet/device-type/', {
    part: part.pk,
    pm_interval_days: 180,
    active: true
  });

  const site = await post(api, 'fleet/site/', {
    name: `PW Site ${uid}`,
    latitude: '37.010000',
    longitude: '-7.930000'
  });

  const deployment = await post(api, 'fleet/deployment/', {
    device_type: deviceType.pk,
    site: site.pk
  });

  const task = await post(api, 'fleet/task/', {
    device: device.pk,
    task_type: 'CORRECTIVE',
    description: `Playwright main UI task ${uid}`
  });

  const trip = await post(api, 'fleet/trip/', {
    title: `PW Trip ${uid}`
  });

  return {
    uid: uid,
    deviceType: deviceType.pk,
    site: { pk: site.pk, reference: site.reference, name: site.name },
    deployment: { pk: deployment.pk, reference: deployment.reference },
    task: { pk: task.pk, reference: task.reference },
    trip: { pk: trip.pk, reference: trip.reference, title: trip.title }
  };
}

test('Fleet - Index', async ({ browser }) => {
  const page = await doCachedLogin(browser, {
    user: adminuser,
    url: 'fleet/index/'
  });

  await page.getByText('Fleet', { exact: true }).first().waitFor();

  // Each panel of the fleet index loads
  for (const [tab, path] of [
    ['Overview', 'overview'],
    ['Alerts', 'alerts'],
    ['Pipeline', 'pipeline'],
    ['Deployments', 'deployments'],
    ['Maintenance', 'maintenance'],
    ['Trips', 'trips'],
    ['Calendar', 'calendar'],
    ['Sites', 'sites'],
    ['Device Types', 'device-types']
  ]) {
    await loadTab(page, tab, true);
    await page.waitForURL(`**/fleet/index/${path}**`);
  }

  // Table filters of the maintenance tasks
  await loadTab(page, 'Maintenance', true);
  await showTableView(page);
  await clearTableFilters(page);
  await setTableChoiceFilter(page, 'Status', 'Proposed');
  await setTableChoiceFilter(page, 'Overdue', 'No');
  await clearTableFilters(page);
});

test('Fleet - Detail Pages', async ({ browser }) => {
  const data = await setupFleetMainData();

  const page = await doCachedLogin(browser, {
    user: adminuser,
    url: `fleet/deployment/${data.deployment.pk}/`
  });

  // Planned deployment: details, custom status hidden, readiness
  await page
    .getByText(`Deployment: ${data.deployment.reference}`)
    .first()
    .waitFor();
  await loadTab(page, 'Deployment Details');
  await page.getByText('Planned').first().waitFor();
  await page.getByText(data.site.name).first().waitFor();
  await expect(page.getByText('Custom Status')).toHaveCount(0);
  await loadTab(page, 'Readiness');
  await loadTab(page, 'Attachments');
  await loadTab(page, 'Notes');

  // Maintenance task
  await navigate(page, `fleet/task/${data.task.pk}/`);
  await page
    .getByText(`Maintenance Task: ${data.task.reference}`)
    .first()
    .waitFor();
  await loadTab(page, 'Task Details');
  await page.getByText('Proposed').first().waitFor();
  await page.getByText('Corrective').first().waitFor();
  await loadTab(page, 'Attachments');
  await loadTab(page, 'Notes');

  // Field trip
  await navigate(page, `fleet/trip/${data.trip.pk}/`);
  await page.getByText(`${data.trip.reference}`).first().waitFor();
  await loadTab(page, 'Trip Details');
  await page.getByText(data.trip.title).first().waitFor();
  await loadTab(page, 'Tasks', true);
  await loadTab(page, 'Kit', true);
  await loadTab(page, 'Attachments');
  await loadTab(page, 'Notes');

  // Site
  await navigate(page, `fleet/site/${data.site.pk}/`);
  await page.getByText(data.site.reference).first().waitFor();
  await loadTab(page, 'Site Details');
  await page.getByText(data.site.name).first().waitFor();
  await loadTab(page, 'Deployments', true);
  await page.getByRole('cell', { name: data.deployment.reference }).waitFor();
  await loadTab(page, 'Alerts', true);
  await loadTab(page, 'Maintenance', true);

  // Device type
  await navigate(page, `fleet/device-type/${data.deviceType}/`);
  await loadTab(page, 'Device Type Details');
  await loadTab(page, 'Data Streams');
  await loadTab(page, 'Checklist');
  await loadTab(page, 'Parts Kit');
  await loadTab(page, 'Deployments', true);

  // Breadcrumb back to the fleet index
  await navigate(page, `fleet/deployment/${data.deployment.pk}/`);
  await page.getByLabel('breadcrumb-0-fleet').click();
  await page.waitForURL('**/fleet/**');
});
