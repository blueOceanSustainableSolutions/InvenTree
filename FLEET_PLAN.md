# Fleet module: implementation plan

This is the implementation plan for the **Fleet** module of the BlueOasis InvenTree fork. It covers device deployments, live monitoring, alerts, preventive maintenance, field trips and field maintenance with component replacement.

The plan is written for an engineer or agent who has **no prior context**. Read it all before writing code. Section 12 lists the open questions to ask the product owner before the phases that depend on them.

---

## 0. Context you need first

### 0.1 Business context
- BlueOasis builds and operates **underwater sensor devices**, for example "Aurora" hydrophone systems. The company **sells the data**, not the devices, so it owns the fleet and is responsible for maintaining it.
- **Exception:** some remote deployments are not serviced. They are still monitored, but no maintenance is planned for them.
- Maintenance happens **mostly in the field** (on site, from a vessel) and sometimes in the office.
- Every maintained device is a **serialized** stock item. Its components are mixed:
  - some are serialized or trackable, such as battery packs and hydrophones;
  - some are bulk consumables, such as O-rings, desiccant and anodes.
- Each device's data is available from BlueOasis's **cloud data dashboard API**. Section 12 lists the questions about its details.
- **Main goal:** a maintenance **plan** driven by the live state of the fleet. That includes data presence per stream, "essential" streams, position, geofences and alerts, plus a forward view of **future deployments**. A device enters the pipeline as soon as its build order is **issued**.

### 0.2 Decisions already made by the product owner
| Decision | Value |
|---|---|
| Plugins | **No plugins.** Everything is built natively in the fork: a Django app plus native React pages. |
| Entry points | The main InvenTree UI gets a native **Fleet** section. A second **Fleet Portal** UI is served on **another port**, with the same backend, database and login. |
| Alerts channel | **Microsoft Teams** first, **email** second, and InvenTree in-app notifications as well. |
| Data dashboard API | Cloud-hosted and reachable from the server. |
| Build orders for fleet devices | **Always quantity 1**. One BO is one device. |
| Pipeline trigger | The deployment plan is created when the BO is **issued** (status PENDING → PRODUCTION), not when it is created. |
| Calibration traceability | Not needed. |
| Regions (Q4, answered 2026-10-07) | **No regions.** Sites and trips have no region field. The manager groups work into trips by **picking tasks freely** (checkbox selection), not by region. Each site has a **nickname** (`Site.name`, e.g. "Algarve #1") shown in the app; it stays with the location when the device there is replaced. |
| Task approval (Q3, answered 2026-10-07) | Technicians close tasks themselves. No manager sign-off, no approval fields or statuses. |
| Customer on deploy (Q5, answered 2026-10-07) | A single default customer company (e.g. "BlueOasis Fleet") via `FLEET_DEFAULT_CUSTOMER`, used when a site has no client. |
| Default PM interval (Q6, answered 2026-10-07) | 180 days (`FleetDeviceType.pm_interval_days` default). Per-type intervals, checklists and kits are entered as data later. |

### 0.3 Repositories and layout
- **Fork (where the code goes):** `c:\Users\diogo\Documents\VSStudio\InvenTree\InvenTree`
  - Branch `blueoasis`, based on InvenTree **1.4.0** (API v511).
  - Remote `blueOceanSustainableSolutions/InvenTree`.
- **Deploy repo:** `c:\Users\diogo\Documents\VSStudio\InvenTree\bOInventreeDeploy`
  - Contains the Dockerfile, Caddyfile, compose file, docs and the legacy `blueoasis_dashboard` plugin.
  - Its `InvenTree/` submodule points to the fork. It is **not initialized** locally; run `git submodule update --init` before `docker compose build`.
- In the rest of this document:
  - `B/` = `src/backend/InvenTree/` in the fork
  - `F/` = `src/frontend/` in the fork
  - `D/` = the deploy repo
- **Version policy:** InvenTree stays at **1.4.0**. Don't upgrade. The production DB was hand-reconciled to this version.
- **Local dev:** `start.cmd` / `stop.cmd` / `Makefile` in the fork call `dev/local-inventree.ps1` (SQLite at `dev/data/inventree.sqlite3`, venv at `dev/venv`).
- **Repo rules** (`CONTRIBUTING.md`):
  - Ruff with Google docstrings.
  - Never edit existing migrations.
  - Migrations must be reversible and portable across databases.
  - Add Python dependencies to `src/backend/requirements.in`, not to `requirements.txt`.
  - Wrap every UI string with Lingui `t`/`<Trans>`.
  - Biome for the frontend.
  - **Never open a PR without human review.**

### 0.4 What already exists and must be reused (don't rebuild it)
- **Component servicing (fork):** `B/stock/components.py`, function `change_components(unit_id, data, user)`, which is `@transaction.atomic`. It is exposed at `GET|POST /api/stock/<pk>/components/`.
  - Actions: `add | remove | destroy | replace`. Fields: `component`, `stock_item`, `quantity`, `replacement_quantity`, `location` (must be non-structural), `disposition` (`keep|damaged|destroyed`), `notes`.
  - It writes the standard tracking entries 30/31/35/36 and propagates the custom USED state (custom key 15).
  - **It requires `unit.build`.** That's fine, because fleet devices come from build orders.
  - The frontend UI for it is `F/src/tables/stock/InstalledItemsTable.tsx`. Its four modals are defined inside the component and are not exported. See P3.
- **Deployed-device convention:** a device is "deployed" when its stock item has a `customer`. Assigning a customer (`StockItem.allocateToCustomer`, tracking code 100 SENT_TO_CUSTOMER) removes the item from "in stock", because `IN_STOCK_FILTER` requires `customer=None`.
  - The legacy plugin widget "Deployed Systems" (`D/plugins/blueoasis_dashboard/blueoasis_dashboard.py`, `deployed_api` around lines 1176–1360) relies on this.
  - **Keep the convention:** deploying sets a customer, and recovering returns the item from the customer. External stock locations are **not** an alternative, because `Part.get_stock_count` includes them by default.
- **Status codes:** `B/stock/status_codes.py` (fork-modified). Free `StockHistoryCode` values start at **130**.
- **Build issue:** `Build._action_issue()` in `B/build/models.py` (around line 750) sets `BuildStatus.PRODUCTION` (20).
  - **Caveat:** `create_build_output()` also flips PENDING → PRODUCTION **without** calling `_action_issue`.
  - **Caveat:** `trigger_event` only reaches plugins, so it can't be used natively. Use Django `post_save` on `Build`.

---

## 1. Architecture overview

```
                    ┌──────────────── Caddy (D/Caddyfile) ────────────────┐
  :8443  ──────────►│  /web/*  → main SPA (index.html entry)              │
  :8444  ──────────►│  "/" → redirect /fleet/ ; /fleet/* → Fleet Portal   │
                    │  /api, /auth, /static, /media shared by both        │
                    └───────────────────────┬─────────────────────────────┘
                                            ▼
                         Django (inventree-server, gunicorn :8082)
                         ├─ existing apps (part, stock, build, …)
                         └─ NEW app B/fleet/  (models, API /api/fleet/…, signals, services)
                                            │
            inventree-worker (django-q2) ───┤ scheduled tasks in B/fleet/tasks.py
                                            │   • poll data platform (every 5 min)
                                            │   • evaluate health / geofence / alerts
                                            │   • daily planning (PM proposals, pipeline risks)
                                            ▼
                     Postgres 17 (same DB)          Cloud Data Dashboard API (HTTPS)
                                            │
                       notifications ───────┴──► Teams Workflows webhook, email, in-app
```

**Principles**
1. **A single Django app, `fleet`,** owns all new tables. Changes to existing apps are kept minimal and listed explicitly (section 9).
2. **Stock stays the source of truth for physical items.** Fleet models reference `StockItem`, `Part`, `Build` and `StockLocation`, and every physical change goes through the existing stock or component functions.
3. **Business logic lives in `fleet/services/`.** API views and tasks stay thin, so the logic can be unit-tested without HTTP.
4. **Raw sensor data stays on the data platform.** InvenTree stores only state: last-seen time per stream, the last position, a downsampled position track, alerts and records.
5. **Two UIs share the same frontend code.**
   - The Fleet Portal is a **second Vite entry** in the same build, so it reuses the API client, auth, forms, tables and renderers.
   - Shared fleet components live in `F/src/fleet/` and are used by both the main-app pages and the portal.

---

## 2. Backend: the `fleet` app

### 2.1 App skeleton
```
B/fleet/
  __init__.py
  apps.py                 # FleetConfig; ready() imports fleet.signals
  models.py
  status_codes.py         # StatusCode classes (auto-registered for custom states)
  serializers.py
  api.py                  # fleet_api_urls
  filters.py
  admin.py
  signals.py              # post_save receivers (Build, StockItem)
  tasks.py                # @scheduled_task functions (auto-collected)
  validators.py           # reference pattern validators/generators
  geo.py                  # haversine distance, point-in-polygon (pure python)
  notify.py               # Teams webhook, email, in-app dispatch
  integrations/
    __init__.py
    base.py               # DataPlatformClient interface + dataclasses
    http_client.py        # real client (configured by settings)
    mock_client.py        # deterministic fake for dev/tests
  services/
    pipeline.py           # BO-issue hook, sync with build/outputs, readiness risks
    deployment.py         # deploy, recover, swap, next PM computation
    monitoring.py         # apply poll results, health, geofence, alerts
    planning.py           # PM proposals, kit suggestion
    maintenance.py        # task lifecycle, component actions, consumables, verify
    trips.py              # trip lifecycle, kit transfer, reconcile
  migrations/
    0001_initial.py
    0002_default_fault_codes.py   # data migration (seed fault codes)
  templates/fleet/        # email + report templates
  fixtures/               # test fixtures (optional)
  test_models.py, test_api.py, test_pipeline.py, test_monitoring.py,
  test_maintenance.py, test_trips.py
```

**Registration**
- Add `'fleet.apps.FleetConfig'` to INSTALLED_APPS in `B/InvenTree/settings.py` (around lines 307–326), **before** `'InvenTree.apps.InvenTreeConfig'`.
- Add `import fleet.api` in `B/InvenTree/urls.py`, and add `path('fleet/', include(fleet.api.fleet_api_urls))` to `apipatterns`, **before** the catch-all `NotFoundView`.
- Add `'fleet'` to `builtin_apps()` in the fork's root `tasks.py` (around lines 319–337), so `invoke dev.test` includes it.
- Tasks are auto-collected: `B/InvenTree/apps.py collect_tasks()` imports `fleet.tasks`.

### 2.2 Permissions: new role `fleet`
Follow the steps in `B/users/ruleset.py`:
1. Add `RuleSetEnum.FLEET = 'fleet'` and a `RULESET_CHOICES` entry.
2. Map **every** `fleet_*` table in `get_ruleset_models()`. The test `users/tests.py::test_model_names` fails if any table is unmapped. Do **not** list auto-created M2M tables (e.g. `fleet_fieldtrip_team`): `apps.get_models()` skips them, so the test reports them as extra.
3. Add `'fleet': 'Role Fleet'` to `_roles` in `B/users/oauth2_scopes.py`. `test_scope_names` checks this.
4. **No new `users` migration is needed.** `B/users/migrations/0007_alter_ruleset_name.py` imports the live `RULESET_CHOICES`, so the migration state always matches the code; `makemigrations --check` confirms it (verified in P0).
5. Frontend: add `fleet` to `UserRoles` and to the label switch in `F/lib/enums/Roles.tsx`.

Usage of the role:
- **View:** manager, technician and read-only users.
- **Change:** technician. Technicians can start, execute and close tasks.
- **Add/Delete:** manager.

Stock operations performed by fleet services run **inside fleet endpoints**. Those endpoints check `fleet.change`, and the stock functions are then called directly, so technicians also need the `stock.change` role.

### 2.3 Global settings
Add these to `SYSTEM_SETTINGS` in `B/common/setting/system.py`, and list them in `F/src/pages/Index/Settings/SystemSettings.tsx` under a new **Fleet** settings panel.

| Key | Default | Notes |
|---|---|---|
| `FLEET_SITE_REFERENCE_PATTERN` | `ST-{ref:03d}` | |
| `FLEET_DEPLOYMENT_REFERENCE_PATTERN` | `DP-{ref:04d}` | |
| `FLEET_TASK_REFERENCE_PATTERN` | `MT-{ref:04d}` | |
| `FLEET_TRIP_REFERENCE_PATTERN` | `TRIP-{ref:04d}` | |
| `FLEET_ALERT_REFERENCE_PATTERN` | `AL-{ref:05d}` | |
| `FLEET_DATA_PROVIDER` | `mock` | choices `mock` / `http` |
| `FLEET_DATA_API_URL` | `''` | URLValidator |
| `FLEET_DATA_API_TOKEN` | `''` | **protected** |
| `FLEET_POLL_INTERVAL_MINUTES` | `5` | The task runs every minute and skips until this many minutes have passed since the last poll |
| `FLEET_NO_CONTACT_HOURS` | `6` | No stream reported, so the device counts as "no contact" (critical) |
| `FLEET_STREAM_MISSING_FACTOR` | `6` | A stream is MISSING after interval × factor (LATE after interval + grace) |
| `FLEET_GEOFENCE_DEFAULT_RADIUS_M` | `200` | |
| `FLEET_GEOFENCE_WARN_PERCENT` | `80` | Beyond this percentage of the radius the device is degraded. An integer, because float settings are not read back as numbers (`common.tests.SettingsTest.test_defaults` fails on them) |
| `FLEET_PLAN_HORIZON_DAYS` | `45` | Proposed preventive maintenance tasks are created inside this horizon |
| `FLEET_PM_DUE_WARNING_DAYS` | `14` | |
| `FLEET_READY_LEAD_DAYS` | `7` | A device must be READY this many days before its target deploy date |
| `FLEET_UNSCHEDULED_REMINDER_DAYS` | `7` | |
| `FLEET_KIT_PARENT_LOCATION` | none | Model setting → StockLocation (structural "Field Kits") |
| `FLEET_WORKSHOP_LOCATION` | none | Model setting → StockLocation ("Workshop / To inspect") |
| `FLEET_DEFAULT_CUSTOMER` | none | Model setting → Company (customer). Used when deploying a site that has no client. |
| `FLEET_TEAMS_WEBHOOK_URL` | `''` | **protected**. A Teams *Workflows* (Power Automate) webhook URL. Classic O365 connectors are retired. |
| `FLEET_ALERT_EMAILS` | `''` | Comma-separated |
| `FLEET_PORTAL_URL` | `''` | For example `https://host:8444/fleet/`. Used for links in notifications. |

Model-type settings use the `'model'` key (e.g. `'stock.stocklocation'`) plus `'model_filters'` (structural / non-structural location, `is_customer`), which gives a dropdown in the settings UI. Secrets use `'protected': True`, which the API returns as `***`. The settings test `common/tests.py::run_settings_check` had an outdated allow-list without these three keys; P0 added them.

### 2.4 Status codes (`fleet/status_codes.py`)
Subclass `generic.states.StatusCode` with members of the form `NAME = value, _('Label'), ColorEnum.x`. Subclasses are auto-registered for custom states and `/api/generic/status/`. Expose each class through `StatusView` in `fleet/api.py`.

```
DeploymentStatus: PLANNED 10 secondary, IN_PRODUCTION 20 primary, READY 30 info,
                  SCHEDULED 40 info, DEPLOYED 50 success, RECOVERED 60 dark, CANCELLED 90 danger
  groups: PIPELINE = [10,20,30,40]; ACTIVE = [50]; CLOSED = [60,90]
TaskStatus:       PROPOSED 10 secondary, SCHEDULED 20 info, IN_PROGRESS 30 primary,
                  COMPLETED 40 success, CANCELLED 90 danger
  groups: OPEN = [10,20,30]
TripStatus:       PLANNING 10, KIT_READY 20, IN_PROGRESS 30, RECONCILING 40, CLOSED 50, CANCELLED 90
AlertStatus:      OPEN 10 danger, ACKNOWLEDGED 20 warning, RESOLVED 30 success
```

Add new `StockHistoryCode` values in `B/stock/status_codes.py` (free range 130+):
- `FLEET_DEPLOYED = 130`
- `FLEET_RECOVERED = 131`
- `FLEET_MAINTENANCE = 132` (generic "maintenance performed" note on the device)

### 2.5 Data model (`fleet/models.py`)
Use the InvenTree mixins:
- `InvenTreeModel` / `MetadataMixin` on every model.
- `ReferenceIndexingMixin` and the reference validator/generator pattern on referenced models. Copy the fork's `QuickBuild` (`B/build/models.py` around line 2565, and `B/build/validators.py`).
- `InvenTreeNotesMixin` and `InvenTreeAttachmentMixin` where noted.
- `InvenTreeCustomStatusModelField` plus `StatusCodeMixin` for statuses (copy how `Build.status` is declared).
- `InvenTreeReportMixin` with a typed `report_context()` where a report is wanted.

Implement `get_api_url()` on every model with `reverse('api-fleet-…-list')`.

```
FleetDeviceType            # marks a Part as a fleet device; holds its maintenance rules
  part            OneToOne Part (assembly, trackable) [unique]
  pm_interval_days         int, default 180
  verify_window_minutes    int, default 30     # how long "Verify data" waits for fresh data
  active                   bool
  (notes)

StreamTemplate             # default data streams for a device type
  device_type FK FleetDeviceType
  key  str (platform stream id, e.g. "hydrophone")  name str
  essential bool  expected_interval_minutes int  grace_minutes int
  unique (device_type, key)

ChecklistTemplateItem      # preventive checklist per device type
  device_type FK, sequence int, text str,
  kind choice: CHECK | MEASUREMENT | PHOTO,  unit str (for measurement), required bool

KitTemplateLine            # parts typically taken for a PM of this device type
  device_type FK, part FK Part, quantity decimal,
  mode choice: ALWAYS | LIKELY

FaultCode                  # seeded by data migration 0002
  code str unique, name str, category choice (POWER, SENSOR, ENCLOSURE, CONNECTOR_CABLE,
  MOORING, FOULING, FIRMWARE_COMMS, EXTERNAL_DAMAGE, OTHER), active bool
  seed: WATER_INGRESS, BIOFOULING, CORROSION, SEAL_FAILURE, BATTERY_DEGRADATION,
        CONNECTOR_DAMAGE, CABLE_DAMAGE, MOORING_DRAG, MOORING_FAILURE, FIRMWARE_FAULT,
        COMMS_FAILURE, SENSOR_DRIFT, VANDALISM_FISHING, OTHER

Site  (reference ST-xxx, notes, attachments, metadata)
  name str (nickname shown in the app, e.g. "Algarve #1"), latitude/longitude decimal(9,6), depth_m decimal null,
  coverage choice: FULL | NO_SERVICE | THIRD_PARTY  (default FULL)
  client FK company.Company (is_customer) null
  project str
  pm_interval_override_days int null
  geofence_radius_m int null  (null → setting default)
  geofence_polygon JSON null  ([[lat,lon],…] closed or open ring)
  active bool

DeviceLink                 # identity of a physical device on the data platform
  stock_item OneToOne StockItem
  platform_id str, unique when not blank   # id used by the data dashboard API (blank = not configured yet, flagged)
  firmware_version str, notes

Deployment  (reference DP-xxxx, status DeploymentStatus, notes, attachments, report mixin)
  device_type FK FleetDeviceType
  build       FK build.Build null, unique when not null   # the BO that produces the device
  device      FK StockItem null     # set once a serial exists (build output) or when assigned
  site        FK Site null           # may be unknown while PLANNED
  deployment_type choice: NEW_STATION | REPLACEMENT | TEMPORARY
  replaces    FK Deployment null     # for REPLACEMENT: the deployment it ends at the same site
  client      FK Company null        # defaults from site.client
  coverage    choice (copied from site at creation; editable)
  target_date date null              # planned deploy date ("when to deploy")
  deployed_at datetime null, recovered_at datetime null
  latitude/longitude/depth_m         # actual nominal position recorded at deployment
  geofence_radius_m int null, geofence_polygon JSON null  # copied from site, editable
  next_pm_date date null, next_pm_manual bool default False
  # live state cache (written by monitoring service only)
  health choice: UNKNOWN | OK | DEGRADED | CRITICAL (default UNKNOWN)
  last_contact datetime null
  last_latitude/last_longitude decimal null, last_position_at datetime null
  distance_from_nominal_m decimal null
  last_polled_at datetime null
  constraint: at most one ACTIVE (DEPLOYED) deployment per device and per site

DataStream                 # per deployment, copied from StreamTemplate on deploy
  deployment FK, key, name, essential, expected_interval_minutes, grace_minutes,
  last_seen datetime null, state choice: UNKNOWN | OK | LATE | MISSING, enabled bool
  unique (deployment, key)

PositionFix                # downsampled track
  deployment FK, timestamp, latitude, longitude, source str
  rule: store if ≥60 min since last stored fix OR moved > 25 m

Alert  (reference AL-xxxxx, status AlertStatus)
  deployment FK null, site FK null, stream FK DataStream null
  alert_type choice: STREAM_LATE | STREAM_MISSING | NO_CONTACT | GEOFENCE_BREACH |
                     PM_DUE | PM_OVERDUE | NOT_READY | BUILD_LATE | NO_DEPLOY_DATE | PARTS_SHORT
  severity choice: INFO | WARNING | CRITICAL
  message str, data JSON
  opened_at, acknowledged_at/by, resolved_at/by, resolution choice: AUTO | TASK | MANUAL
  task FK MaintenanceTask null      # task that resolved it / was created from it
  dedupe_key str                     # e.g. "dp:12:STREAM_MISSING:hydrophone"
  constraint: unique open (status != RESOLVED) per dedupe_key

FieldTrip  (reference TRIP-xxxx, status TripStatus, notes, attachments, report mixin)
  title, start_date, end_date null, vessel str,
  team M2M User, responsible FK User null,
  kit_location FK StockLocation null   # auto-created child of FLEET_KIT_PARENT_LOCATION

TripKitLine
  trip FK, part FK Part, quantity_planned decimal,
  source choice: ALWAYS | LIKELY | ALERT | MANUAL, note

MaintenanceTask  (reference MT-xxxx, status TaskStatus, notes, attachments, report mixin)
  task_type choice: PREVENTIVE | CORRECTIVE | DEPLOYMENT | RECOVERY | SWAP | INSPECTION
  device FK StockItem               # the serialized unit worked on (required)
  deployment FK Deployment null     # context (null = workshop task on a unit in stock)
  site FK Site null
  trip FK FieldTrip null
  due_date date null, scheduled_date date null
  started_at/by, completed_at/by, technicians M2M User, labour_minutes int null
  as_found str, summary str
  verification JSON null            # result of verify step
  verification_override_reason str  # if closed without passing verification
  follow_up_of FK self null
  alerts M2M Alert                  # alerts this task addresses

ChecklistResult
  task FK, template_item FK ChecklistTemplateItem null, sequence, text, required bool (copied from the template),
  result choice: PENDING | OK | ISSUE | NA, value str, note str, fault_code FK null

MaintenanceAction          # one physical/logical action performed in a task
  task FK
  action choice: REPLACE | REMOVE | ADD | DESTROY | CONSUME | REPAIR | CLEAN |
                 FIRMWARE | REPOSITION | OTHER
  component_out FK StockItem null   # removed / destroyed / replaced item
  component_in  FK StockItem null   # installed item (after split)
  part FK Part null, quantity decimal null
  disposition str null, destination FK StockLocation null
  fault_code FK null, checklist_result FK null, note str
  created_at, created_by
```

### 2.6 Services (business rules)

**`services/pipeline.py`**
- `on_build_saved(build)` is called from the `post_save` receiver on `build.Build` in `signals.py`. Guard it with `InvenTree.ready.canAppAccessDatabase(allow_test=True)` and `isImportingData()`.
  1. If `build.part` has an active `FleetDeviceType` and `build.status == PRODUCTION` and no Deployment exists with `build=build`, create one:
     - `Deployment(status=IN_PRODUCTION, device_type, build, deployment_type=NEW_STATION, target_date=None)`
     - This covers both `_action_issue` and the `create_build_output` path, and it is idempotent.
  2. If the build quantity is ≠ 1, still create **one** deployment, and log a warning on it (`metadata.warning`). The product owner says it is always 1.
  3. If `build.status == CANCELLED`, the linked PLANNED or IN_PRODUCTION deployment goes to CANCELLED.
  4. If `build.status == COMPLETE`, call `sync_build_output(deployment)`.
- `sync_build_output(dep)`:
  - If `dep.device` is null and the build has an output StockItem (`StockItem.objects.filter(build=dep.build)`), set the device and create a `DeviceLink` if one is missing (platform_id empty → flag it).
  - If the output is complete (`is_building=False`) and the status is IN_PRODUCTION, set it to **READY**.
  - **Also call this from a scheduled task every 10 minutes.** Build outputs are created with bulk operations, so a `post_save` receiver on StockItem is not reliable.
- Manual planning:
  - `create_planned(device_type, site, target_date, …)` creates a deployment with status PLANNED.
  - `create_build_for(dep, user)` creates a Build (quantity 1, part = `device_type.part`, target_date = `dep.target_date - FLEET_READY_LEAD_DAYS`) and links it. Issuing the BO later moves the deployment to IN_PRODUCTION through the hook.
  - `assign_existing_device(dep, stock_item)` covers refurbished units: the item must be in stock and serialized, with the same part or a variant. The deployment goes to READY.
- `readiness_risks(dep)` returns a list of `{code, severity, message}` and is computed, not stored:
  - `BUILD_LATE`: `build.target_date > dep.target_date`.
  - `NOT_READY`: `target_date - today <= FLEET_READY_LEAD_DAYS` and the status is below READY.
  - `PARTS_SHORT`: any BuildLine where required − allocated − consumed > available stock of the part. Reuse the annotations the BuildLine serializer already computes (`available_stock` etc. in `B/build/serializers.py`); check them before writing new queries.
  - `NO_DEPLOY_DATE`: `target_date` is null and the deployment has existed for more than `FLEET_UNSCHEDULED_REMINDER_DAYS`.

**`services/deployment.py`**
- `schedule(dep, trip)`: requires status READY. Sets SCHEDULED, links it to the trip through a DEPLOYMENT task, and adds the device to the trip kit.
- `deploy(dep, user, lat, lon, depth, deployed_at)`, inside a transaction:
  1. Set the position, copy the geofence from the site if empty, set `deployed_at`, status DEPLOYED.
  2. Copy `StreamTemplate`s to `DataStream`s.
  3. Assign the stock item to a customer (`dep.client` or `site.client` or `FLEET_DEFAULT_CUSTOMER`) with `device.allocateToCustomer(customer, user=user, notes=f'Deployed {dep.reference} at {site.reference}')`. This keeps the legacy "deployed = has customer" convention.
  4. Add a tracking entry `FLEET_DEPLOYED` with deltas `{'deployment': dep.pk, 'site': site.pk}`.
  5. If `dep.replaces` is set, call `recover(dep.replaces, …)`.
  6. Compute `next_pm_date`.
- `recover(dep, user, destination=FLEET_WORKSHOP_LOCATION)`:
  - Set status RECOVERED and `recovered_at`.
  - Call `device.return_from_customer(location, user, notes)`.
  - Add a `FLEET_RECOVERED` tracking entry.
  - Auto-resolve the deployment's open alerts (resolution AUTO).
- `compute_next_pm(dep)`:
  - Skip if `next_pm_manual` is set.
  - `base` = the completion date of the latest COMPLETED task of type PREVENTIVE, DEPLOYMENT or SWAP, otherwise `deployed_at`.
  - `interval` = `site.pm_interval_override_days` or `device_type.pm_interval_days`.
  - `next_pm_date` = `base + interval`.
  - For NO_SERVICE coverage, set `next_pm_date = None`.

**`services/monitoring.py`**
- `poll_all()` is run by a task every minute. It returns early unless `FLEET_POLL_INTERVAL_MINUTES` have passed since the last poll, which is stored in a cache or setting key.
  - It fetches `client.get_status([platform_ids])` in batches for all DEPLOYED deployments, then calls `apply_status(dep, status)`.
  - Client errors must not raise alerts per device. Log them, and if the platform has failed for 3 consecutive polls, send **one** "data platform unreachable" notification.
- `apply_status(dep, status)`:
  1. **Streams.** For each stream, update `last_seen`. The state is OK, or LATE if `age > expected + grace`, or MISSING if `age > expected × FLEET_STREAM_MISSING_FACTOR`.
  2. **Contact.** `last_contact` = the max of the stream `last_seen` values. If it is older than `FLEET_NO_CONTACT_HOURS`, raise a NO_CONTACT alert.
  3. **Position.** If present, update the `last_*` fields and `distance_from_nominal_m` (`geo.haversine`), and store a downsampled `PositionFix`. The device is outside its geofence when it is outside the polygon (if set) or beyond the radius.
  4. **Health.**
     - CRITICAL: an essential stream is MISSING, or NO_CONTACT, or outside the geofence.
     - DEGRADED: an essential stream is LATE, or a non-essential stream is MISSING, or `distance > radius × WARN_PERCENT / 100`.
     - OK otherwise.
     - UNKNOWN if the device has never been seen.
  5. **Alerts** via `open_or_update_alert(dedupe_key, …)`:
     - STREAM_LATE: WARNING, essential streams only.
     - STREAM_MISSING: CRITICAL if essential, WARNING otherwise.
     - NO_CONTACT: CRITICAL.
     - GEOFENCE_BREACH: CRITICAL.
     - When the condition clears, auto-resolve (resolution AUTO), and notify only for CRITICAL alerts.
     - A new alert notifies on OPEN. An escalation from WARNING to CRITICAL notifies again.
- `verify_now(dep, since)` is called by the maintenance verify step. It fetches live status for this one device and returns:
  - per stream: `{key, essential, last_seen, fresh: last_seen >= since}`
  - `inside_geofence`
  - `passed` = all essential streams fresh AND inside the geofence (or no position source)

**`integrations/base.py`** (the interface is fixed now; the HTTP mapping depends on the API spec in section 12)
```python
@dataclass
class StreamStatus:  key: str; last_seen: datetime | None
@dataclass
class Position:      latitude: float; longitude: float; timestamp: datetime; source: str = 'platform'
@dataclass
class DeviceStatus:  platform_id: str; streams: list[StreamStatus]; position: Position | None; raw: dict
class DataPlatformClient(ABC):
    def get_status(self, platform_ids: list[str]) -> dict[str, DeviceStatus]: ...
    def list_devices(self) -> list[dict]: ...   # optional, for linking/import UI
def get_client() -> DataPlatformClient: # by FLEET_DATA_PROVIDER
```
- `mock_client.py` must be deterministic, so tests can control its output by setting class attributes.
- `http_client.py` uses `requests`, a timeout of 10 seconds, and a bearer token from the setting.

**`services/planning.py`** (daily task plus on demand)
- `propose_pm_tasks()`:
  - Applies to each DEPLOYED deployment with coverage FULL and `next_pm_date <= today + FLEET_PLAN_HORIZON_DAYS`.
  - If no open PREVENTIVE task exists for it, create a `MaintenanceTask(PROPOSED, PREVENTIVE, device, deployment, site, due_date=next_pm_date)`.
- `pm_alerts()`:
  - PM_DUE (INFO): due within `FLEET_PM_DUE_WARNING_DAYS`.
  - PM_OVERDUE (WARNING): past due and no completed task.
  - NO_SERVICE deployments get none.
- `pipeline_alerts()`: raises or resolves alerts for the `readiness_risks` codes. Teams is notified only for BUILD_LATE and NOT_READY.
- `suggest_kit(trip)`: for each planned task in the trip:
  - add KitTemplateLines with mode ALWAYS;
  - add LIKELY lines from the template, plus parts from the device's history that failed (fault codes on REPLACE actions) in ≥ 2 of the last N tasks for that device type. Keep this simple in v1.
  - For DEPLOYMENT tasks, add the device itself. Show it as a kit line with its serial.
  - Output: `TripKitLine`s, plus availability = stock outside kit locations.

**`services/maintenance.py`**
- `schedule_tasks(trip, tasks)`: tasks go from PROPOSED to SCHEDULED, with `trip` and `scheduled_date` set.
- `start(task, user)`: status IN_PROGRESS. Instantiates `ChecklistResult` rows from the device type's template, plus the checklist items for the task type.
- `component_action(task, user, data)`: wraps `stock.components.change_components(task.device.pk, data, user)` **without changing its behaviour**.
  - Prefix the notes: `f'[{task.reference}] {note}'`.
  - Default `location` (remove/replace) is the trip's kit location child "Removed". Create it lazily as a non-structural child of `trip.kit_location`. Without a trip, use `FLEET_WORKSHOP_LOCATION`.
  - Record a `MaintenanceAction` with `component_out`/`component_in` from the result. `change_components` returns the installed item; check `ComponentActionResultSerializer` and the function's return value.
  - **Traceability:** before the call, capture `max_id = StockItemTracking.objects.aggregate(Max('pk'))`. After the call, add `deltas['maintenance'] = task.pk` to every new tracking row (`pk > max_id`) whose item is the unit or a component involved. This happens in the same transaction.
  - Also add one `FLEET_MAINTENANCE` tracking entry on the device when the task completes.
- `consume(task, user, stock_item, quantity, note)`:
  - The bulk consumable must be in the trip kit location, or any location if there is no trip.
  - Call `stock_item.take_stock(quantity, user, notes=f'[{task.reference}] …')` and record a CONSUME action. Check the exact `take_stock` signature in `B/stock/models.py`.
- `reposition(task, user, lat, lon)`: updates the deployment's nominal position and moves the geofence centre. It records a REPOSITION action that stores the old position in `metadata`.
- `verify(task)`: calls `monitoring.verify_now(task.deployment, since=task.started_at)` and stores the result in `task.verification`.
- `complete(task, user, override_reason=None)`:
  1. Requires every **required** checklist item to be non-PENDING.
  2. Requires `verification.passed`, or an `override_reason`. When the override is used, auto-create a CORRECTIVE follow-up task (`follow_up_of`).
  3. Set status COMPLETED and `completed_at`.
  4. Resolve linked alerts (resolution TASK).
  5. Call `compute_next_pm(deployment)`.
  6. Notify Teams with a short summary: actions and parts replaced.
- `create_from_alert(alert, user)` creates a CORRECTIVE task (SCHEDULED if a date is given, otherwise PROPOSED), links the alert, and acknowledges it.
- **Swap** is a task of type SWAP. Its steps:
  1. A REPLACEMENT deployment with status READY or SCHEDULED, and the same site.
  2. Calling `deploy(new_dep, replaces=old_dep)` recovers the old device into the trip "Removed" location. At reconcile it moves on to the workshop.

**`services/trips.py`**
- `create_trip(...)`:
  - Creates a non-structural `StockLocation` named `TRIP-xxxx`, under `FLEET_KIT_PARENT_LOCATION`.
  - Creates a child location `TRIP-xxxx / Removed`.
- `prepare_kit(trip, lines, user)`: transfers the chosen stock items into `trip.kit_location` with `StockItem.move()`. This mirrors `/api/stock/transfer/` and writes STOCK_MOVE 20. The status becomes KIT_READY, and the Teams message "TRIP-xxxx ready".
- `start_trip`: status IN_PROGRESS.
- `reconcile(trip, user, returns)`:
  - `returns` maps leftover kit stock to a destination location; the default is each part's `default_location`.
  - Items in "Removed" go to `FLEET_WORKSHOP_LOCATION` and keep their DAMAGED/DESTROYED status.
  - Status goes to CLOSED once the kit location is empty and every task is COMPLETED or CANCELLED (unfinished tasks are moved back to PROPOSED with `trip=None`).
- Trip report: an `InvenTreeReportMixin` context with tasks, actions, parts used and alerts resolved.

**`notify.py`**
- `notify(event_code, title, text, link, severity, obj=None)` sends to three channels:
  1. **Teams:** POST an Adaptive Card to `FLEET_TEAMS_WEBHOOK_URL` through `offload_task`. Include the title, a fact set and an "Open" button linking to `FLEET_PORTAL_URL` + the path. A failure is logged and not raised.
  2. **Email:** `InvenTree.helpers_email.send_email(subject, body, recipients=FLEET_ALERT_EMAILS, html_message=…)`, only when `is_email_configured()`.
  3. **In-app:** `common.notifications.trigger_notification(obj, category=f'fleet.{event_code}', targets=<users with fleet.view role>)`.
     - This uses InvenTree's **built-in** UI notification mechanism. It is part of core, not a custom plugin, and is acceptable.
     - If it pulls in plugin machinery that is disabled in production, skip this channel.
- Events notified:
  - alert opened or escalated (CRITICAL);
  - BUILD_LATE / NOT_READY;
  - trip kit ready;
  - task completed;
  - data platform unreachable.

### 2.7 Scheduled tasks (`fleet/tasks.py`)
| Function | Schedule | Calls |
|---|---|---|
| `fleet_poll` | `@scheduled_task(ScheduledTask.MINUTES, 1)` | `monitoring.poll_all()`, which applies its own interval guard |
| `fleet_sync_pipeline` | MINUTES 10 | `sync_build_output` for deployments with status IN_PRODUCTION or PLANNED that have a build |
| `fleet_daily_planning` | DAILY | `propose_pm_tasks`, `pm_alerts`, `pipeline_alerts`, then prune `PositionFix` older than 2 years |

### 2.8 REST API (`/api/fleet/…`)
Follow the patterns in `B/build/api.py`:
- `ListCreateAPI` / `RetrieveUpdateDestroyAPI` with `DataExportViewMixin` and `OutputOptionsMixin`.
- `SEARCH_ORDER_FILTER`, a FilterSet, `ordering_field_aliases` for the reference fields, and `meta_path(Model)`.
- `StatusView` for each status class.
- Serializers: `InvenTreeModelSerializer` with `FilterableSerializerMixin` / `OptionalField` for `*_detail` fields, and `InvenTreeCustomStatusSerializerMixin` for status fields.
- Action endpoints: `CreateAPI` with an input serializer that calls a service, wraps it in `transaction.atomic`, and returns the updated object. Role `fleet.change` unless noted.

```
device-type/            (+ <pk>/)          CRUD; nested: stream-template/, checklist-item/, kit-line/
site/                   (+ <pk>/)          filters: coverage, active, client, has_active_deployment
device-link/            (+ <pk>/)
deployment/             (+ <pk>/)          filters: status(+group pipeline/active), site, health,
                                           coverage, client, device, build, pm_due_before, unscheduled,
                                           has_open_alerts; output option: risks (readiness_risks)
deployment/<pk>/create-build/   POST
deployment/<pk>/assign-device/  POST {stock_item}
deployment/<pk>/deploy/         POST {latitude, longitude, depth_m, deployed_at}
deployment/<pk>/recover/        POST {location?, notes}
deployment/<pk>/verify/         POST {since?}         → verify_now result (no state change)
deployment/<pk>/streams/        GET/PATCH list (enable/disable, essential, interval)
deployment/<pk>/track/          GET PositionFix list (?since=)
deployment/map/                 GET compact list for map: pk, ref, site, lat/lon (last or nominal),
                                nominal, radius/polygon, health, open_alert_count
overview/                       GET KPI counts: deployed, health buckets, pm_due_30, pm_overdue,
                                open_alerts by severity, pipeline: in_production, ready,
                                to_deploy_30, unscheduled
alert/                  (+ <pk>/)          filters: status, severity, type, deployment, site
alert/<pk>/acknowledge/         POST
alert/<pk>/resolve/             POST {note}
alert/<pk>/create-task/         POST {scheduled_date?, trip?}
task/                   (+ <pk>/)          filters: status, type, site, trip, device,
                                           deployment, due_before, assigned_to_me
task/<pk>/start/                POST
task/<pk>/checklist/            GET/PATCH ChecklistResult rows
task/<pk>/component-action/     POST same body as /api/stock/<pk>/components/ + fault_code, checklist_result
task/<pk>/consume/              POST {stock_item, quantity, note}
task/<pk>/reposition/           POST {latitude, longitude}
task/<pk>/verify/               POST
task/<pk>/complete/             POST {labour_minutes, summary, override_reason?}
task/<pk>/cancel/               POST
task/<pk>/actions/              GET MaintenanceAction list
trip/                   (+ <pk>/)          filters: status, date range, team member (me)
trip/<pk>/add-tasks/            POST {tasks:[pk]}   (also deployments → creates DEPLOYMENT tasks)
trip/<pk>/suggest-kit/          POST → regenerates TripKitLine (non-MANUAL)
trip/<pk>/kit/                  GET kit lines + availability + what is currently in kit location
trip/<pk>/prepare-kit/          POST {items:[{stock_item, quantity}]}
trip/<pk>/start/                POST
trip/<pk>/reconcile/            POST {returns:[{stock_item, location}]}
fault-code/                     list (+ admin CRUD)
calendar/                       GET ?start&end → events: deployments(target_date), tasks(due/scheduled),
                                trips(start..end)   (for FullCalendar)
status/<class>/                 StatusView per status class
```

### 2.9 Admin, reports, data migration
- Register every model in `admin.py`, using the InvenTree admin patterns.
- Reports: `Deployment`, `MaintenanceTask` (service report) and `FieldTrip` (trip report) get a `report_context()` with a typed return. Add default templates under `fleet/templates/fleet/` and document how to upload them as report templates.
- Add a management command `fleet_import_existing` for a **one-time import** of today's deployed systems:
  - For each stock item with a `customer`, whose part is a fleet device type, create a Site (named after the customer or the stock notes; the manager edits it later) and a DEPLOYED Deployment.
  - `deployed_at` = the last 60/100 tracking date (same logic as the plugin's `deployed_api`).
  - Add a dry-run flag, and run it with the product owner.

---

## 3. Frontend: shared building blocks (`F/src/fleet/`)

Register these model types in `F/lib/enums/ModelType.tsx`, with a `ModelInformation` entry each in `F/lib/enums/ModelInformation.tsx` (the entry is mandatory) and a renderer in `F/src/components/render/Instance.tsx` (in a new `F/src/components/render/Fleet.tsx`):
- `fleetsite`
- `fleetdeployment`
- `fleettask`
- `fleettrip`
- `fleetalert`
- `fleetdevicetype`

Each entry sets `url_overview`/`url_detail` under `/fleet/...`, `api_endpoint`, and an icon. Add new icons to `F/src/functions/icons.tsx` (tabler icons: `IconBuildingLighthouse`, `IconRoute`, `IconAlertTriangle`, `IconTool`, `IconSailboat`). Add status classes to `F/src/defaults/backendMappings.tsx` `statusCodeList`. Add every endpoint to `F/lib/enums/ApiEndpoints.tsx` (`fleet_site_list = 'fleet/site/'`, …).

**Shared components** (used by both UIs):
- `HealthBadge`, `CoverageBadge`, `RiskBadges`
- `StreamStatusList`: one row per stream showing the essential flag, the age since last seen, and the state colour.
- `FleetMap`: **leaflet + react-leaflet**. These are new dependencies; add them with `yarn add leaflet react-leaflet @types/leaflet` and check that the react-leaflet version supports React 19.
  - OpenStreetMap tiles with attribution.
  - Coloured pins by health, geofence circles or polygons, the last-position marker plus a line to the nominal position when they differ, and a popup card with a link.
  - Props: `deployments`, `onSelect`, `showTrack?`.
- `OverviewKpis`: tiles from `overview/`.
- Tables, using `InvenTreeTable` and the patterns in `F/src/tables/build/QuickBuildTable.tsx`:
  - `DeploymentTable` (modes: pipeline / active / all)
  - `SiteTable`, `AlertTable` (row actions: acknowledge, resolve, create task), `TaskTable`, `TripTable`
  - `TripKitTable`, `ActionTable`, `ChecklistEditor`
- Forms in `F/src/forms/FleetForms.tsx`, using `useCreateApiFormModal` / `useEditApiFormModal` with the patterns in `F/src/forms/QuickBuildForms.tsx`: site, deployment (plan), deploy, recover, alert→task, trip, prepare kit, consume, complete task.
- **Component service hook:** refactor `F/src/tables/stock/InstalledItemsTable.tsx`.
  - Extract its four modals into `useComponentServiceForms({ unit, url, extraFields, onSuccess })`.
  - The url defaults to `stock/:id/components/`. InstalledItemsTable keeps its current behaviour.
  - The fleet task execution screen calls the same hook with `url = fleet task/<pk>/component-action/` and extra fields `fault_code`, `checklist_result`, and the default location "TRIP / Removed".
  - The stock-item `related field` already supports **scan-to-pick** through `RelatedModelField` (`ScanButton`) when barcode settings are on. Make sure `BARCODE_IN_FORM_FIELDS` is documented for technicians.
- `TaskExecution` component: the full field flow, used by both UIs. It shows:
  - the live stream panel;
  - the checklist with OK / Issue / N/A, and an issue leading to a fault code and an optional action;
  - actions (Replace, Remove, Add, Destroy, Consume, Reposition, Note) and photo attachments (the attachments API with `model_type='maintenancetask'`);
  - the Verify button and the Complete dialog.
- `FleetCalendar`: reuse `F/src/components/calendar/OrderCalendar.tsx` (FullCalendar) with events from `fleet/calendar/`.

---

## 4. Main InvenTree UI (`/web/fleet/…`)

**Navigation:**
- Tab `{name: 'fleet', title: t\`Fleet\`, icon, visible: user.hasViewRole(UserRoles.fleet)}` in `F/src/defaults/links.tsx getNavTabs`.
- Drawer entry in `F/src/components/nav/NavigationDrawer.tsx`.
- Spotlight action in `F/src/defaults/actions.tsx`.
- Routes in `F/src/router.tsx`, lazy-loaded with `Loadable`.

| Route | Page | Content |
|---|---|---|
| `/fleet/index/:panel` | `FleetIndex` (pattern `BuildIndex.tsx`: `PageDetail` + `PanelGroup pageKey='fleet-index'`) | Panels: **Overview** (KPIs, map, open alerts), **Pipeline** (DeploymentTable pipeline mode + "Unscheduled" filter), **Deployments** (active), **Sites**, **Alerts**, **Maintenance** (TaskTable; table/calendar via `SegmentedControlPanel`), **Trips**, **Calendar**, **Device types** |
| `/fleet/site/:id/*` | `SiteDetail` | details; map; deployments history; maintenance history; alerts; uptime stats; attachments; notes |
| `/fleet/deployment/:id/*` | `DeploymentDetail` | status and badges; actions (plan/create build/assign device/deploy/recover/edit); panels: details, health and streams, map and track, alerts, maintenance, risks (pipeline), attachments, notes |
| `/fleet/task/:id/*` | `TaskDetail` | details; `TaskExecution` (when IN_PROGRESS); actions list; checklist; verification; attachments; print service report |
| `/fleet/trip/:id/*` | `TripDetail` | details; tasks; kit (suggest / prepare / current contents); reconcile wizard; attachments; print trip report |
| `/fleet/device-type/:id/*` | `DeviceTypeDetail` | PM interval; stream templates; checklist template; kit template |

**Panels added to existing pages**
- `StockDetail.tsx`, a **Fleet** panel shown when the part has a FleetDeviceType: current deployment card (site, health, last contact, next PM), deployment history, maintenance history, DeviceLink (platform id) editor.
- `BuildDetail.tsx`, a **Deployment plan** panel or badge when a Deployment is linked: site, target date, and the risks.
- `PartDetail.tsx`, a **Fleet device** panel: create or edit the FleetDeviceType for this part.

**Dashboard widgets:** add native `QueryCountDashboardWidget`s in `F/src/components/dashboard/DashboardWidgetLibrary.tsx` for:
- open critical alerts;
- PM overdue;
- unscheduled pipeline;
- tasks assigned to me.

---

## 5. Fleet Portal (second UI, `:8444` → `/fleet/`)

### 5.1 Build and serve
- **Vite:** in `F/vite.config.ts` add `build.rollupOptions.input = { index: 'index.html', fleet: 'fleet.html' }`. Keep the same `outDir`. The manifest will then contain the keys `index.html` and `fleet.html`.
  - Create `F/fleet.html`, a copy of `index.html` whose script is `/src/portal/main.tsx`.
  - Check that `yarn build` still produces the main `index.html` entry unchanged.
- **Django:**
  - `B/web/templatetags/spa_helper.py`: give `spa_bundle` an optional `entry='index.html'` argument. It currently hard-codes `manifest_data.get('index.html')`.
  - `spa_settings`: accept an optional `base_url` override.
  - Add `B/web/templates/web/fleet.html` with `{% spa_bundle entry='fleet.html' %}` and `{% spa_settings base_url='fleet' %}`.
  - In `B/web/urls.py`, add `path('fleet/', include([... re_path('.*', fleet_view)]))`, with `fleet_view = ensure_csrf_cookie(TemplateView.as_view(template_name='web/fleet.html'))`.
  - Check the `InvenTreeHostSettingsMiddleware` behaviour (INVE-E7) for `/fleet/`. A different port on the same hostname passes the lax hostname check.
- **Portal app (`F/src/portal/`):**
  - `main.tsx` mirrors `F/src/main.tsx`: the same `setApiDefaults`, theme and i18n providers.
  - **Don't** use `MainView`'s mobile blocker. The portal must work on phones.
  - `PortalApp.tsx`: `BrowserRouter basename='fleet'`, routes below, and the auth routes reused from `F/src/pages/Auth/*` (`/login`, `/logged-in`, `/logout`, `/mfa` …) inside `LoginLayoutComponent`.
  - Protected routes reuse `ProtectedRoute` / `checkLoginState`.
  - Layout: Mantine `AppShell`. Desktop gets a header and side nav. Phone width (< 768 px) gets a bottom tab bar and full-width cards, with large touch targets.
  - Everything reuses the components in section 3.
- **Session:** cookies are per host, not per port, so `:8443` and `:8444` share the session and CSRF cookies. Logging in or out on one affects both.

### 5.2 Portal screens
| Route | Screen | Users |
|---|---|---|
| `/` (Overview) | KPI tiles; **map** with health pins and geofences; open alerts list (acknowledge or create task); deployments table (site nickname, device, health, last data, days deployed, next PM, coverage) with filters for client, health, PM due and coverage | Manager |
| `/pipeline` | Grouped list: **Unscheduled** (set date or site inline), **Scheduled** (by target date, with risk badges), **Ready**. Actions: plan deployment, create BO, assign existing unit, add to trip | Manager |
| `/plan` | Upcoming PM and open corrective tasks within the horizon, listed by due date with the site nickname and the map; the manager **ticks the tasks to group** (no region grouping) and uses **Create field trip** / **Add to trip**; calendar toggle (deployments, tasks and trips) | Manager |
| `/trips`, `/trips/:id` | Trip header and status steps (Planning → Kit ready → In progress → Reconciling → Closed); tasks by site; **Kit** tab (suggested vs available vs taken; Prepare kit); **Reconcile** tab (leftovers back to stock, removed parts to workshop); report | Manager |
| `/my` | Technician home: my trips today and upcoming; open tasks assigned to me | Technician |
| `/deployment/:id` | Device card: site, serial, live streams, map snippet, installed components (read-only tree via `GET stock/<pk>/components/`), alerts, history timeline, **Start maintenance** / **Deploy** / **Recover** | Both |
| `/task/:id` | **TaskExecution** (section 3): checklist → actions (Replace / Remove / Add / Consume / Reposition / Destroy) with barcode scan → **Verify data** → **Close** | Technician |
| `/alerts` | All alerts with filters | Manager |
| `/sites`, `/sites/:id` | Site list and detail (uptime, visits, devices over time) | Manager |

---

## 6. Key workflows, end to end (acceptance scenarios)

Write these as backend service and API tests, and as Playwright tests where noted.

1. **Pipeline from BO issue**
   1. Part P has a FleetDeviceType.
   2. Create BO (qty 1) → no Deployment yet.
   3. Issue the BO → Deployment IN_PRODUCTION, linked to the build, with no site or date.
   4. Pipeline "Unscheduled" shows it.
   5. Set the site and target date.
   6. Create the output with serial AUR-0161 → the sync links the device.
   7. Complete the build → READY.
   8. If `build.target_date > target_date`, the risk is BUILD_LATE and a Teams notification is sent (mock the webhook).
2. **Cancel BO:** the deployment becomes CANCELLED.
3. **Deploy**
   1. A READY deployment is added to a trip, which creates a DEPLOYMENT task and puts the device in the kit.
   2. Deploy with a position → status DEPLOYED, the stock item gets a customer (tracking 100 + 130), streams are copied, and `next_pm_date` is computed.
4. **Monitoring**
   1. The mock platform reports the essential stream older than interval × factor → stream MISSING, health CRITICAL, one Alert OPEN, one notification.
   2. A second poll doesn't duplicate the alert.
   3. The stream comes back → alert RESOLVED (AUTO).
   4. Position 340 m away with radius 200 → GEOFENCE_BREACH.
5. **Scheduled maintenance with replacement**
   1. `next_pm_date` falls inside the horizon → the daily task proposes a PM task.
   2. The manager creates a trip with it → SCHEDULED, with a kit suggestion.
   3. Prepare kit → items in `TRIP-xxxx`.
   4. Technician: Start → checklist issue "Battery degradation" → `component-action` replace BAT-0331 with BAT-0347 (from the kit), disposition `damaged`. Expected results:
      - BAT-0347 is installed in the device.
      - BAT-0331 is in `TRIP-xxxx / Removed` with status DAMAGED.
      - The tracking rows have `deltas.maintenance = task.pk`.
      - A MaintenanceAction row exists.
   5. Consume 1 O-ring kit → kit quantity decreases.
   6. Verify (mock: fresh data) → passed.
   7. Complete → COMPLETED, the geofence alert is resolved (TASK), `next_pm_date` moves forward, and a Teams message is sent.
   8. Reconcile the trip → leftovers go back to default locations and BAT-0331 goes to the workshop → trip CLOSED.
6. **Remove without replacement** disables the matching DataStream when the user ticks "no longer essential" in the dialog.
7. **Swap:** after `deploy(new, replaces=old)`, the old deployment is RECOVERED, the old device is out of the customer and in the trip's Removed location, the site keeps both in its history, and the new device is DEPLOYED.
8. **NO_SERVICE coverage:** monitoring alerts still fire, but there are no PM proposals and no PM alerts.
9. **Permissions:**
   - A user without the `fleet` role gets 403 on `/api/fleet/*`.
   - A technician (fleet.change + stock.change) can execute tasks but can't delete sites.
10. **Portal (Playwright):** log in on the portal path, open the overview, open a deployment, run the task flow with mocked data.

---

## 7. Implementation phases (one PR-sized milestone each; stop for review after each)

**Definition of done for every phase** (product owner, 2026-10-08), in addition to the phase's own criteria:
1. The **whole** backend suite passes on Linux (`make test`, see section 8), including tests that were already failing before the phase. Fix them; don't list them as known failures.
2. The deploy Dockerfile overlay is updated **in the same phase** for every new or changed backend file (section 9).
3. The deploy image builds with the phase's code, and Django starts inside it (`check`, `migrate` on an empty database, `makemigrations --check`).


| Phase | Scope | Done when |
|---|---|---|
| **P0 Foundations** | App skeleton, registration, role `fleet` (ruleset, scopes, frontend `Roles.tsx`; no users migration needed), settings, status codes, **all models** plus `0001`/`0002` migrations, admin, StockHistoryCode 130–132 | `fleet`, `users.tests`, `common` settings tests and `makemigrations --check` pass; plus the definition of done above (whole suite green, Dockerfile overlay updated, image builds and starts) |
| **P1 Sites, device types, pipeline** | Services `pipeline` and `deployment` (minus monitoring); APIs for device-type, site, deployment (+ actions), calendar, overview (pipeline part); signals plus the sync task; frontend model types, renderers, Fleet tab, FleetIndex (Pipeline, Deployments, Sites, Device types), Site/Deployment/DeviceType detail, Build and Part panels; `fleet_import_existing` command | Scenarios 1, 2 and 3 (deploy without a trip) pass |
| **P2 Monitoring and alerts** | Integration client (mock + http skeleton), `monitoring` service, poll task, streams, positions, geofence (`geo.py` with unit tests), alerts plus the alert API, `notify.py` (Teams, email, in-app), FleetMap, Overview panel, Alerts panel, StockDetail Fleet panel | Scenario 4 passes; a Teams card was checked manually against a test channel |
| **P3 Maintenance** | Checklist and kit templates, `planning` (PM proposals and alerts), `maintenance` service and task API, `useComponentServiceForms` refactor, TaskExecution, Task detail, service report | Scenarios 5 (without trip), 6, 8 and 9 pass; InstalledItemsTable behaviour unchanged (existing `stock/test_components.py` still passes) |
| **P4 Field trips** | `trips` service and API, kit suggestion, prepare and reconcile, trip detail, plan selection (tick tasks → trip), trip report, swap | Scenarios 5 (full) and 7 pass |
| **P5 Fleet Portal** | Vite second entry, Django template, URL and spa_helper changes, portal shell and screens (section 5.2), responsive layout | Scenario 10 passes; manually checked on a phone width |
| **P6 Deployment and docs** | Caddy, compose and env changes (section 9; Dockerfile COPY lines are added by each phase), migration runbook, `D/docs/13-fleet.md` user guide, retire the plugin's Deployed Systems widget **only after sign-off** | Staging deploy verified end to end |

---

## 8. Testing and quality commands
- Backend tests: `make test` (all apps) or `make test TESTS="fleet users.tests"`, i.e. `dev/local-inventree.ps1 test [labels]`. Needs Docker Desktop running.
  - It runs `dev/ci-test.sh` in the `inventree/inventree:1.4.0` image (Linux), on a **copy** of the checkout, with the same preparation as the upstream CI job: git repo, `gettext`, fresh SQLite database, plugins enabled, `invoke migrate`, `invoke static`, `check_migration_files.py`, then `invoke dev.test --check --translations`.
  - Do **not** treat native Windows runs as the reference. WeasyPrint/label printing, `file://C:\…` static paths and the sample printer plugin fail on Windows (46 failures on 2026-10-08), and running against the dev database breaks tests that rely on content type ids.
  - In CI (Linux): `invoke dev.test --runtest=fleet` (and the other apps).
- Migrations:
  - `python src/backend/InvenTree/manage.py makemigrations --check --dry-run`
  - Reversibility: `migrate fleet zero`, then `migrate`.
- Schema: `invoke dev.schema`. Review the diff for `/api/fleet/`.
- Lint:
  - `ruff check src/backend/ && ruff format src/backend/`
  - `npx @biomejs/biome@1.9.4 check src/frontend`
- Frontend: `cd src/frontend && yarn run extract && yarn run compile && yarn build`. The `tsc` step catches missing `ModelInformation` or renderer entries.
- Playwright: follow `F/tests/pages/pui_*.spec.ts` with `doCachedLogin`, and add `F/tests/pages/pui_fleet.spec.ts`.
- **API version:** the fork has never bumped `INVENTREE_API_VERSION` (still 511), and the deploy repo pins "API 511". Keep 511 unless the product owner decides otherwise, and don't add a version entry.

---

## 9. Changes outside `fleet/` (complete list, so the Dockerfile overlay stays in sync)
**Fork backend:**
- `B/InvenTree/settings.py` (INSTALLED_APPS)
- `B/InvenTree/urls.py` (API include)
- `B/users/ruleset.py`, `B/users/oauth2_scopes.py` (no users migration)
- `B/common/setting/system.py`
- `B/stock/status_codes.py` (codes 130–132)
- `B/web/urls.py`, `B/web/templatetags/spa_helper.py`, `B/web/templates/web/fleet.html`
- Root `tasks.py`: `builtin_apps()`. This is dev only and isn't needed in the image.
- `B/common/tests.py` (settings test allow-list: `model`, `model_filters`, `protected`). Test only; not needed in the image.
- `B/common/models.py` (P0: `Attachment.rename()` closes the old file before deleting it; fixes `test_attachments` on Windows). Copied into the image to keep the overlay equal to the fork diff.
- `B/build/test_custom_requirements.py` (P0: test fix, the variant's parent must be a template). Test only.
- `B/generic/states/tests.py`, `B/stock/test_api.py` (P0: status counts for the 4 fleet status classes and the fork's `DISASSEMBLED` stock status), `B/web/tests.py` (P0: `test_spa_bundle` always restores the manifest). Test only.
- `dev/local-inventree.ps1`, `dev/ci-test.sh`, `Makefile` (P0: `test` action, Linux in Docker). Dev only and git-ignored (local tooling).

**Fork frontend:**
- New: `F/fleet.html`, `F/src/portal/**`, `F/src/fleet/**`, `F/src/pages/fleet/**`, `F/src/forms/FleetForms.tsx`, `F/src/components/render/Fleet.tsx`.
- Modified: `vite.config.ts`, `router.tsx`, `defaults/links.tsx`, `defaults/actions.tsx`, `components/nav/NavigationDrawer.tsx`, `lib/enums/{ModelType,ModelInformation,ApiEndpoints,Roles}.tsx`, `components/render/Instance.tsx`, `defaults/backendMappings.tsx`, `functions/icons.tsx` (and `lib/types/Icons.tsx` if needed), `pages/stock/StockDetail.tsx`, `pages/build/BuildDetail.tsx`, `pages/part/PartDetail.tsx`, `tables/stock/InstalledItemsTable.tsx` (refactor only), `components/dashboard/DashboardWidgetLibrary.tsx`, `pages/Index/Settings/SystemSettings.tsx`, `package.json` / `yarn.lock` (leaflet).

**Deploy repo (`D/`):**
- **`Dockerfile`:** the backend overlay is a **hand-maintained list of individual files**, and must always equal `git diff --name-only 0a9a8b1c5 HEAD -- src/backend/InvenTree` (minus tests). Otherwise the image can contain a file that imports code which isn't copied, and the server crashes on start.
  - **Added in P0:** the whole `fleet/` directory, `InvenTree/settings.py`, `InvenTree/urls.py`, `users/oauth2_scopes.py`, `common/models.py` (`users/ruleset.py`, `stock/status_codes.py` and `common/setting/system.py` were already copied).
  - **To add in P5:** `web/urls.py`, `web/templatetags/spa_helper.py`, `web/templates/web/fleet.html`.

  The frontend stage already copies the whole `web/static/web` output, which now includes the `fleet.html` entry assets. A simpler alternative is to generate the COPY list with `git diff --name-only 0a9a8b1c5 HEAD -- src/backend`. Propose it, but don't silently change the overlay strategy.
- **`Caddyfile`:** add
  ```
  https://:8444 {
      tls /certs/cert.pem /certs/key.pem
      redir / /fleet/ 302
      import common
  }
  ```
  `redir /` with the exact path `/` only matches the root.
- **`docker-compose.yml`:** proxy ports gain `"8444:8444"`.
- **`.env` / `.env.example`:** add every `https://<host>:8444` origin (LAN IP, Tailscale name) to `INVENTREE_TRUSTED_ORIGINS`. Without them, CSRF rejects POSTs from the portal.
- **Migrations:** `INVENTREE_AUTO_UPDATE=False`, so migrations **don't run on boot**. After deploying, run `docker compose exec inventree-server invoke migrate`, then `./scripts/collect-static.sh`. Document this in `D/DEPLOY.md` and the new `D/docs/13-fleet.md`. Back up first (`D/BACKUPS.md`).
- **Settings to configure after deploy:** the kit parent location (structural "Field Kits"), the workshop location, the default customer (e.g. company "BlueOasis Fleet"), the Teams webhook, the alert emails, the portal URL, and the data provider, URL and token. Also assign the `fleet` role to the manager and technician groups.

---

## 10. Non-goals for v1
- Offline field capture (PWA sync). Design the endpoints to be idempotent so it can be added later. See question 2 in section 12.
- Storing raw sensor data or time series in InvenTree.
- Calibration certificates and regulated traceability.
- Customer billing or invoicing for maintenance.
- MTBF and reliability analytics beyond simple counts. Planned for v2, once the data exists.

## 11. Gotchas found during research
- `trigger_event` is plugin-only and disabled by default (`ENABLE_PLUGINS_EVENTS`). Use Django signals.
- `BuildEvents.OUTPUT_CREATED` is never triggered, and build outputs are bulk-created. Don't rely on `post_save` for StockItem; use the sync task.
- `create_build_output()` can move a BO to PRODUCTION without `_action_issue()`. The `post_save` hook on Build covers both paths.
- The scheduled-task interval is fixed at decoration time, so a setting-driven interval needs the guard pattern.
- `change_components` requires `unit.quantity == 1`, `not is_building` and `unit.build`. Removal `location` must be **non-structural**, which is why the trip has a non-structural "Removed" child location.
- The custom state **USED (custom key 15)** exists only in the DB. Installing a USED component marks the unit USED. Keep this behaviour.
- Every new DB table must be in `get_ruleset_models()` or the user tests fail.
- `ModelInformation` and the renderer lookup are exhaustive dictionaries. Adding a `ModelType` without them breaks `tsc`.
- `messages.ts` (Lingui) is gitignored. The Docker build runs `extract` and `compile`.
- The frontend `MainView` blocks narrow viewports unless `mobile_mode` allows them. The portal must not use that view.
- The deploy repo's `InvenTree/` submodule is not initialized locally. Run `git submodule update --init` before `docker compose build`.
- `InvenTreeCustomStatusModelField` adds a `status_custom_key` field by itself. In a `CreateModel` migration, list `status_custom_key` **before** `status` (as `order/0118_transferorder.py` does), otherwise the migration fails with `duplicate column name: status_custom_key`. `makemigrations` generates them in the wrong order; fix new migrations by hand before applying them.
- Partial unique constraints (one active deployment per device/site, one unresolved alert per dedupe key) are enforced on Postgres and SQLite; MySQL ignores them, so services must also check.
- Local test runs: `invoke` only works from Git Bash with `--disable-pty`, uses `python3` from the root `.venv`, and prints a harmless INVE-W9 warning. Ruff 0.15.12 (the pre-commit pin) is not installed in either venv; use `uvx ruff@0.15.12`. Generated migrations are left unformatted, as in the rest of the fork.
- P0 fixed every failing test on the unmodified fork: the fork had added `StockStatus.DISASSEMBLED` without updating the two status-count tests; `test_custom_requirement_can_allow_variants` created a variant of a non-template part; `Attachment.rename()` left the old file open (Windows); `web.tests.test_spa_bundle` could leave the real `manifest.json` replaced by `broken` on Windows (now restored in a `finally` with `replace()`). The rest came from the test environment (section 8). New status classes change `generic.states.tests.test_all_states` (count of classes): update it when adding one.

## 12. Open questions: ask the product owner before the phase that needs them
1. **Data dashboard API spec (blocks P2 `http_client.py`):**
   - base URL and auth method;
   - the endpoint giving the latest timestamp per device per stream;
   - device and stream identifiers;
   - the GPS position endpoint;
   - rate limits;
   - whether a batch call or webhooks exist.

   Build P2 against the mock until this is answered.
2. **Field connectivity:** is mobile data always available on site? v1 assumes online only.
3. ~~**Approval:** can technicians close tasks themselves (assumed yes), or does a manager sign off?~~ **Answered:** technicians close them.
4. ~~**Regions:** free text (assumed) or a managed list?~~ **Answered:** no regions; tasks are grouped by free selection; sites have nicknames (see 0.2).
5. ~~**Customer on deploy:** which company to use when a site has no client?~~ **Answered:** one default company via `FLEET_DEFAULT_CUSTOMER`.
6. **Default PM interval and checklist** per device type (Aurora PI5 75 m, Hydrophone 3 m, Aurora multichannel), and the standard kit per PM. *Partly answered:* the default is 180 days; per-type values, checklists and kits are still needed (as data) before P3.
7. **Retire the legacy plugin's "Deployed Systems" widget** after P6? It also uses the customer convention, so it keeps working in the meantime.
