import { type APIRequestContext, expect } from '@playwright/test';

/*
 * Test data helpers for the fleet tests.
 *
 * The fleet never creates a deployment out of nothing: a unit gets its row in
 * the deployment list from its build order (or from the stock sync, a
 * scheduled task which does not run in these tests). So a test unit is built
 * through a build order: issuing it opens the deployment, completing it
 * links the serialized output and marks the deployment READY.
 */

/** POST and return the JSON body, failing with the response text */
export async function post(api: APIRequestContext, url: string, data: any) {
  const response = await api.post(url, { data: data });
  expect(response.ok(), `POST ${url}: ${await response.text()}`).toBeTruthy();
  return response.json();
}

/** GET a list and return the JSON body */
export async function list(api: APIRequestContext, url: string, params: any) {
  const response = await api.get(url, { params: params });
  expect(response.ok(), `GET ${url}: ${await response.text()}`).toBeTruthy();
  return response.json();
}

/** A non-structural stock location for the test devices (reused) */
export async function testLocation(api: APIRequestContext): Promise<number> {
  const name = 'Fleet Playwright Workshop';
  const existing = await list(api, 'stock/location/', { name: name });

  if (existing.length > 0) {
    return existing[0].pk;
  }

  const location = await post(api, 'stock/location/', {
    name: name,
    description: 'Fleet tests',
    structural: false
  });

  return location.pk;
}

/**
 * Build one serialized unit of a fleet part through a build order, and
 * return the unit and its READY deployment (no site, no date).
 */
export async function buildFleetUnit(
  api: APIRequestContext,
  {
    part,
    serial,
    uid,
    location
  }: { part: number; serial: string; uid: string; location: number }
): Promise<{ device: any; deployment: any }> {
  const build = await post(api, 'build/', {
    part: part,
    quantity: 1,
    reference: `BO-${uid}`,
    title: `Fleet test unit ${serial}`
  });

  await post(api, `build/${build.pk}/issue/`, {});

  await post(api, `build/${build.pk}/create-output/`, {
    quantity: 1,
    serial_numbers: serial,
    location: location
  });

  const outputs = await list(api, 'stock/', {
    build: build.pk,
    is_building: true
  });
  expect(outputs.length).toBe(1);
  const device = outputs[0];

  await post(api, `build/${build.pk}/complete/`, {
    outputs: [{ output: device.pk }],
    location: location
  });

  await post(api, `build/${build.pk}/finish/`, {
    accept_unallocated: true,
    accept_incomplete: true
  });

  // The completed build order links its output to the deployment
  let deployment: any = null;

  await expect
    .poll(
      async () => {
        const rows = await list(api, 'fleet/deployment/', {
          build: build.pk
        });
        deployment = rows[0] ?? null;
        return deployment?.device ?? null;
      },
      { message: `deployment of build order ${build.reference}` }
    )
    .toBe(device.pk);

  return { device: device, deployment: deployment };
}
