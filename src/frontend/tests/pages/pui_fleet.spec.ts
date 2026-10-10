import { type APIRequestContext, type Page, expect } from '@playwright/test';
import { createApi } from '../api.ts';
import { test } from '../baseFixtures.ts';
import { adminuser } from '../defaults.ts';

/*
 * Fleet Portal (FLEET_PLAN scenario 10): log in on the portal path, open the
 * overview, open a deployment and run the task flow with mocked data.
 *
 * The test creates its own data through the API (a trackable device part, one
 * serialized unit, a customer, a device type with an essential stream and a
 * checklist item, a deployed deployment with a platform id, and a preventive
 * task), so it does not depend on the demo dataset. The data platform is the
 * built-in mock (FLEET_DATA_PROVIDER = mock), which reports every stream as
 * fresh, so "Verify data" passes.
 */

const portalUrl = '/fleet';

type FleetData = {
  uid: string;
  serial: string;
  location: number;
  deployment: { pk: number; reference: string };
  task: { pk: number; reference: string };
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

/** A non-structural stock location for the test device (reused) */
async function testLocation(api: APIRequestContext): Promise<number> {
  const name = 'Fleet Playwright Workshop';
  const existing = await list(api, 'stock/location/', { name: name });

  if (existing.length > 0) {
    return existing[0].pk;
  }

  const location = await post(api, 'stock/location/', {
    name: name,
    description: 'Fleet Portal tests',
    structural: false
  });

  return location.pk;
}

/** Create a deployed device with a preventive task, through the API */
async function setupFleetData(): Promise<FleetData> {
  const api = await createApi({});
  const uid = `${Date.now()}`.slice(-8);
  const serial = `PW${uid}`;

  const location = await testLocation(api);

  const part = await post(api, 'part/', {
    name: `Fleet PW Unit ${uid}`,
    description: 'Fleet Portal test device',
    trackable: true,
    assembly: true,
    active: true
  });

  await post(api, 'stock/', {
    part: part.pk,
    quantity: 1,
    serial_numbers: serial,
    location: location
  });

  const items = await list(api, 'stock/', { part: part.pk, serial: serial });
  expect(items.length).toBe(1);
  const device = items[0];

  const client = await post(api, 'company/', {
    name: `Fleet PW Client ${uid}`,
    is_customer: true
  });

  const deviceType = await post(api, 'fleet/device-type/', {
    part: part.pk,
    pm_interval_days: 180,
    active: true
  });

  await post(api, 'fleet/device-type/stream-template/', {
    device_type: deviceType.pk,
    key: 'hydrophone',
    name: 'Hydrophone',
    essential: true,
    expected_interval_minutes: 60,
    grace_minutes: 30
  });

  await post(api, 'fleet/device-type/checklist-item/', {
    device_type: deviceType.pk,
    sequence: 1,
    text: 'Check the seals',
    kind: 'CHECK',
    required: true,
    task_type: ''
  });

  const planned = await post(api, 'fleet/deployment/', {
    device_type: deviceType.pk,
    client: client.pk
  });

  await post(api, `fleet/deployment/${planned.pk}/assign-device/`, {
    stock_item: device.pk
  });

  const deployed = await post(api, `fleet/deployment/${planned.pk}/deploy/`, {
    latitude: '37.010000',
    longitude: '-7.930000'
  });
  expect(deployed.status).toBe(50);

  // Link the device to the (mock) data platform
  const links = await list(api, 'fleet/device-link/', {
    stock_item: device.pk
  });
  expect(links.length).toBe(1);

  const linked = await api.patch(`fleet/device-link/${links[0].pk}/`, {
    data: { platform_id: `pw-${uid}` }
  });
  expect(linked.ok()).toBeTruthy();

  const task = await post(api, 'fleet/task/', {
    device: device.pk,
    task_type: 'PREVENTIVE',
    description: 'Playwright preventive maintenance'
  });
  expect(task.deployment).toBe(planned.pk);

  return {
    uid: uid,
    serial: serial,
    location: location,
    deployment: { pk: deployed.pk, reference: deployed.reference },
    task: { pk: task.pk, reference: task.reference }
  };
}

/** Return the test device to stock (so its deployment is closed) */
async function recoverDevice(data: FleetData) {
  const api = await createApi({});

  await api.post(`fleet/deployment/${data.deployment.pk}/recover/`, {
    data: { location: data.location, notes: 'Fleet Portal test' }
  });
}

/** Log in through the login page of the portal */
async function portalLogin(page: Page) {
  await page.goto(`${portalUrl}/`);
  await page.waitForURL('**/fleet/login');

  await page
    .getByRole('textbox', { name: 'login-username' })
    .fill(adminuser.username);
  await page
    .getByRole('textbox', { name: 'login-password' })
    .fill(adminuser.testcred);
  await page.getByRole('button', { name: 'Log in' }).click();

  // Back to the portal overview (not the main UI)
  await page.waitForURL(/\/fleet\/?$/);
  await page.getByRole('heading', { name: 'Fleet Overview' }).waitFor();
}

/** The page must not scroll sideways */
async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test('Fleet Portal - Scenario 10', async ({ browser }) => {
  const data = await setupFleetData();

  const page = await browser.newPage();

  try {
    // Log in on the portal path
    await portalLogin(page);

    // Overview: KPI tiles, map and the deployed devices
    await page.getByText('Deployed', { exact: true }).first().waitFor();
    await page.getByRole('heading', { name: 'Deployed Devices' }).waitFor();
    await expect(page.locator('.leaflet-container').first()).toBeVisible();

    // Open the deployment from the deployed devices table
    await page.getByLabel('table-search-input').last().fill(data.serial);
    await page.waitForLoadState('networkidle');
    await page
      .getByRole('cell', { name: data.deployment.reference })
      .first()
      .click();
    await page.waitForURL(`**/fleet/deployment/${data.deployment.pk}**`);

    // Device card: status, serial, live streams
    await page
      .getByRole('heading', {
        name: `Deployment ${data.deployment.reference}`
      })
      .waitFor();
    await page.getByText('Deployed', { exact: true }).first().waitFor();
    await page.getByText(`#${data.serial}`).first().waitFor();
    await page.getByText('Hydrophone').first().waitFor();

    // The other tabs of the device card
    await page.getByRole('tab', { name: 'Components' }).click();
    await page.getByText('No installed components').waitFor();
    await page.getByRole('tab', { name: 'History' }).click();
    await page.getByText('Deployed', { exact: true }).first().waitFor();

    // Start maintenance: the open task of the device
    await page.getByRole('button', { name: 'Start Maintenance' }).click();
    await page.getByLabel(`task-card-${data.task.reference}`).click();
    await page.waitForURL(`**/fleet/task/${data.task.pk}**`);

    await page
      .getByRole('heading', { name: `Task ${data.task.reference}` })
      .waitFor();
    await page.getByText('Proposed').first().waitFor();

    // Start the task
    await page.getByRole('button', { name: 'Start Task' }).click();
    await page.getByRole('button', { name: 'Submit' }).click();
    await page.getByText('In Progress').first().waitFor();

    // Checklist: answer the required item
    await page.getByText('Check the seals').waitFor();
    await page.getByText('OK', { exact: true }).first().click();
    await page.waitForLoadState('networkidle');

    // Verify data (mock platform: fresh data) and complete
    await page.getByRole('button', { name: 'Verify data' }).click();
    await page.getByText('Fresh data from every essential stream').waitFor();

    await page.getByRole('button', { name: 'Complete' }).click();
    await page
      .getByLabel('text-field-summary')
      .fill('Seals checked, data verified');
    await page.getByRole('button', { name: 'Submit' }).click();

    await page.getByText('Completed').first().waitFor();
    await page.getByRole('tab', { name: 'Actions' }).waitFor();
  } finally {
    await recoverDevice(data);
  }
});

test('Fleet Portal - Device state', async ({ browser }) => {
  const data = await setupFleetData();

  const page = await browser.newPage();

  try {
    await portalLogin(page);

    await page.goto(`${portalUrl}/deployment/${data.deployment.pk}`);
    await page
      .getByRole('heading', {
        name: `Deployment ${data.deployment.reference}`
      })
      .waitFor();

    // Dock the device (P7): badge, note and no data check
    await page.getByRole('button', { name: 'Set State' }).click();
    await page.getByLabel('choice-field-state').click();
    await page.getByRole('option', { name: 'Docked' }).click();
    await page
      .getByLabel('text-field-note', { exact: true })
      .fill('On the quay for the winter');
    await page.waitForTimeout(200);
    await page.getByRole('button', { name: 'Submit' }).click();

    await page.getByText('Device state updated').waitFor();
    await page.getByText('Docked', { exact: true }).first().waitFor();
    await page.getByText('On the quay for the winter').first().waitFor();

    // Clear it again
    await page.getByRole('button', { name: 'Set State' }).click();
    await page.getByLabel('choice-field-state').click();
    await page.getByRole('option', { name: 'None (clear the state)' }).click();
    await page.waitForTimeout(200);
    await page.getByRole('button', { name: 'Submit' }).click();

    await page.getByText('Device state updated').last().waitFor();
    await expect(page.getByText('On the quay for the winter')).toHaveCount(0);
  } finally {
    await recoverDevice(data);
  }
});

test('Fleet Portal - Phone width', async ({ browser }) => {
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true
  });

  // The login page fits the screen
  await page.goto(`${portalUrl}/login`);
  await page.getByRole('textbox', { name: 'login-username' }).waitFor();
  await expectNoHorizontalScroll(page);

  await portalLogin(page);

  // No "mobile viewport" blocker: the portal works on phones
  await expect(page.getByText('Mobile viewport detected')).toHaveCount(0);

  // Bottom tab bar instead of the side navigation
  await expect(page.getByLabel('portal-tab-/my')).toBeVisible();
  await expect(page.getByLabel('portal-nav-/my')).toBeHidden();
  await expectNoHorizontalScroll(page);

  // Technician home
  await page.getByLabel('portal-tab-/my').click();
  await page.waitForURL('**/fleet/my');
  await page.getByRole('heading', { name: 'My Work' }).waitFor();
  await page.getByText('My Tasks').waitFor();
  await expectNoHorizontalScroll(page);

  // Large touch targets in the tab bar
  const tab = await page.getByLabel('portal-tab-/my').boundingBox();
  expect(tab?.height ?? 0).toBeGreaterThanOrEqual(44);

  // The other screens are under "More"
  await page.getByLabel('portal-tab-more').click();
  await page.getByRole('menuitem', { name: 'Pipeline' }).click();
  await page.waitForURL('**/fleet/pipeline');
  await page.getByRole('heading', { name: 'Deployment Pipeline' }).waitFor();
  await expectNoHorizontalScroll(page);

  await page.getByLabel('portal-tab-/trips').click();
  await page.getByRole('heading', { name: 'Field Trips' }).waitFor();
  await expectNoHorizontalScroll(page);
});

test('Fleet Portal - Links from the main UI shape', async ({ browser }) => {
  const page = await browser.newPage();
  await portalLogin(page);

  // A main UI fleet path opens the matching portal screen
  await page.goto(`${portalUrl}/index/alerts`);
  await page.waitForURL('**/fleet/alerts');
  await page.getByRole('heading', { name: 'Alerts' }).waitFor();

  await page.goto(`${portalUrl}/fleet/index/maintenance`);
  await page.waitForURL('**/fleet/plan');
  await page.getByRole('heading', { name: 'Maintenance Plan' }).waitFor();

  // Other paths open the main UI
  await page.goto(`${portalUrl}/part/category/index/`);
  await page.waitForURL('**/web/part/category/index/**');
});
