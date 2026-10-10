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
| Source of the deployed list (answered 2026-10-08) | **No import.** The devices already in InvenTree *are* the list. Every serialized stock item of a part marked as a fleet device type (or a variant of it) that **has a customer** is a deployed device, and gets its deployment record **automatically** (option A; items of other parts with a customer are ignored). When the customer is removed (returned), the deployment is closed automatically. See section 2.10 (phase P2b). |
| Site and position (answered 2026-10-08) | **A site is optional.** Each device gets its own coordinates (latitude, longitude, depth) on its deployment. Automatically found devices start **without a site and without a position**; both stay empty until a user adds them. Nothing may be required to have a site to be deployed, monitored or planned. |
| Sales visibility (Q8, answered 2026-10-08) | **Every device of a fleet device type that is in a build order linked to a sale (sales order) must be visible in the Fleet list.** Scope chosen: **option (b) only**. A fleet build order linked to a sales order enters the pipeline (PLANNED) as soon as it is **created**, before it is issued; other build orders still enter at issue. No sales-order column (a), and no rows for allocated-but-unshipped units (c). See section 2.10. |

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
  helpers.py              # (P1) model/int settings readers, to_local_date()
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
    stock_sync.py         # (P2b) deployments follow the stock customers
    device_state.py       # (P7) internal device states (section 2.11)
  migrations/
    0001_initial.py
    0002_default_fault_codes.py   # data migration (seed fault codes)
    0003_deployment_creation_date.py   # (P1) Deployment.creation_date
    0004_p3_task_description.py        # (P3) ChecklistTemplateItem.task_type, MaintenanceTask.description
    0005_p4_trip_kit_device.py         # (P4) TripKitLine.stock_item / task, source DEVICE
    0006_p7_device_state.py            # (P7) DeviceLink.state / state_note / state_changed_at / state_changed_by
  management/commands/
    fleet_sync_stock.py           # (P2b) stock-driven deployments, --dry-run (replaces P1's fleet_import_existing)
    fleet_install_reports.py      # (P3) installs the report templates below (--update)
  templates/fleet/        # report templates (P3: fleet_service_report.html, P4: fleet_trip_report.html)
  fixtures/               # test fixtures (optional)
  test_models.py, test_api.py, test_pipeline.py, test_stock_sync.py,
  test_monitoring.py, test_maintenance.py, test_trips.py
```

**Status:** P0, P1, P2 and P2b are implemented. Present today: `models.py`, `status_codes.py`, `validators.py`, `helpers.py`, `admin.py`, `apps.py` (connects `signals.py`), `signals.py`, `tasks.py` (`fleet_sync_pipeline`, `fleet_poll`, `fleet_daily_planning`), `serializers.py`, `filters.py`, `api.py`, `geo.py`, `notify.py`, `integrations/` (base, mock, http), `services/pipeline.py`, `services/deployment.py`, `services/monitoring.py`, `services/stock_sync.py`, the `fleet_sync_stock` command, and the tests `test_models.py`, `test_pipeline.py`, `test_api.py`, `test_stock_sync.py`, `test_geo.py`, `test_monitoring.py`. P2b removed the P1 import command and `test_import.py`. P3 added `services/planning.py`, `services/maintenance.py`, migration `0004`, the `fleet_install_reports` command, `templates/fleet/fleet_service_report.html` and `test_maintenance.py`. P4 added `services/trips.py`, migration `0005`, `templates/fleet/fleet_trip_report.html` and `test_trips.py`. Every file of the skeleton now exists. P5 added only `test_portal.py` (the portal is served by the `web` app, see section 5.1). P7 added `services/device_state.py` and migration `0006`.

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

**Changed in P2:**
- `FLEET_DATA_API_URL`, `FLEET_TEAMS_WEBHOOK_URL` and `FLEET_PORTAL_URL` use `fleet.validators.validate_optional_url` (http/https, may be empty, host names without a TLD such as `https://bo-server:8444/fleet/` allowed). P0 had used the core `BaseURLValidator`, which is the *site URL* validator: it rejects any value other than `SITE_URL` whenever `SITE_URL` is configured (as in production), so these settings could not be saved on the server.
- A hidden setting `_FLEET_POLL_STATE` (JSON, default `{}`) holds the last poll time and the consecutive-failure counter. The default cache is per process (locmem), so it cannot be used for this.

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
  creation_date date auto_now_add     # (P1, migration 0003) used by the NO_DEPLOY_DATE reminder
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

**As built in P3** (migration `0004_p3_task_description`, two `AddField`s; no table, so no ruleset change):
- `ChecklistTemplateItem.task_type` (choices of the task types, **blank = every task type**, default `PREVENTIVE`). A task gets the items of its own type plus the blank ones when it starts. This is how "the checklist items for the task type" (2.6 `start`) is modelled.
- `MaintenanceTask.description` (250 chars): what needs to be done. PM proposals get "Preventive maintenance", tasks from an alert get the alert message.
- `TaskType` is now a module-level `TextChoices` in `fleet/models.py` (`MaintenanceTask.TaskType` is an alias), so `ChecklistTemplateItem` can use it. The choices are unchanged, so it caused no migration.
- `get_api_url()` added to `StreamTemplate`, `ChecklistTemplateItem`, `KitTemplateLine`, `DataStream`, `ChecklistResult` and `MaintenanceAction`. InvenTree's OPTIONS metadata needs it for every **writable** related field (e.g. `checklist_result`, `disable_stream` in the task forms).
- `MaintenanceTask.report_context()` also returns `display_name` (site nickname, else serial), `alerts` and `technicians`, and `site` falls back to the deployment's site.

**As built in P4** (migration `0005_p4_trip_kit_device`: two `AddField`s and one `AlterField`; no new table, so no ruleset change):
- `TripKitLine.stock_item` (FK StockItem, null, SET_NULL): the device of a "device to deploy" line, shown with its serial.
- `TripKitLine.task` (FK MaintenanceTask, null, SET_NULL, `related_name='kit_lines'`): the task which needs the line (device lines only). Taking the task off the trip deletes its lines.
- `TripKitLine.Source` gained `DEVICE` ("Device to deploy"). `ALERT` stays unused: alerts carry no part information.
- `TripKitLine.get_api_url()` (`api-fleet-trip-kit-line-list`).
- `FieldTrip.report_context()` also returns `actions` (all actions of the trip tasks), `parts_used` (from `FieldTrip.parts_used()`: one row per part installed or consumed, with quantity and installed serials), `alerts_resolved` (alerts whose `task` is on the trip) and `team`.

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
- **As built in P1:**
  - `on_build_saved` also moves a PLANNED deployment linked to the build (from `create_build_for`) to IN_PRODUCTION when its BO is issued, so there is still one deployment per BO. Device types are matched on the exact part (`active=True`); variants of a device-type part don't create deployments.
  - `sync_build_output` picks completed outputs first (`order_by('is_building', 'pk')`) and also catches up a PLANNED deployment whose BO is already in production.
  - `assign_existing_device` is allowed from PLANNED, IN_PRODUCTION, READY or SCHEDULED, but is refused while the linked BO is still open (PENDING / PRODUCTION / ON_HOLD), because that BO produces the device. The unit must be serialized and in stock, the device-type part or a variant, and not in another pipeline or active deployment. A SCHEDULED deployment stays SCHEDULED; the others become READY.
  - Risk severities: BUILD_LATE WARNING, NOT_READY WARNING (CRITICAL once the target date has passed), PARTS_SHORT WARNING (only while the BO is open), NO_DEPLOY_DATE INFO. BUILD_LATE and PARTS_SHORT are only raised while the BO is open. `readiness_risks` returns `[]` outside the PIPELINE statuses.
  - `create_build_for` titles the BO `Fleet deployment DP-xxxx (site name)`, sets `issued_by=user`, and leaves it PENDING.

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
- **As built in P1:**
  - `deploy()` accepts READY **or** SCHEDULED. It needs a device, a site and a customer, and the device must be in stock. Latitude, longitude and depth default to the site's values.
  - ~~To change in P2b: the site becomes optional in `deploy()`~~ **Done in P2b**, see below.
  - `deploy()` recovers `dep.replaces` **first** (step 5 runs before step 1). The partial unique constraint allows one DEPLOYED deployment per site, so the old one has to be RECOVERED before the new one becomes DEPLOYED in the same transaction. It then refuses if the site still has another DEPLOYED deployment.
  - `deploy()` locks the row and refreshes the caller's instance, so the object passed in reflects the new state.
  - `recover()` needs a **non-structural** location (the given one, else `FLEET_WORKSHOP_LOCATION`), or it raises. Without a trip (P1) the device goes straight to the workshop. P4 passes the trip's "Removed" location.
  - Deploy and recover don't create a MaintenanceTask (no trip in P1), so `compute_next_pm` uses `deployed_at` as the base until P3/P4 create DEPLOYMENT tasks.
  - `fleet.helpers.to_local_date()` converts aware and naive datetimes (tests run with `USE_TZ=False`).
- **As built in P2b:**
  - `deploy()` no longer needs a site. The position comes from the arguments (latitude **and** longitude), else the position already on the deployment (e.g. from *Set position*), else the site's. With none of them it stays empty; depth works the same way. The site geofence (radius, polygon) is copied only when there is a site. Without one, monitoring uses `FLEET_GEOFENCE_DEFAULT_RADIUS_M`, but only once a position exists. The "site already occupied" check runs only with a site, so any number of site-less deployments can be DEPLOYED.
  - `deploy()` now stores the customer it used in `deployment.client` (deployment client, else site client, else `FLEET_DEFAULT_CUSTOMER`), so the stock sync sees no change afterwards. Without a site, the `FLEET_DEPLOYED` tracking deltas have `site: null` and the note omits the site.
  - New `set_position(dep, latitude, longitude, depth_m=None, geofence_radius_m=None)`. It is refused for RECOVERED/CANCELLED deployments and needs both coordinates; depth and radius are kept when not given. It validates only the position fields (`clean_fields`).
  - New `resolve_open_alerts(dep, user=None, now=None)` (AUTO), shared by `recover()` and the stock sync.
  - `compute_next_pm` is unchanged: it already used the device-type interval when there is no site.

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

- **As built in P2:**
  - Only DEPLOYED deployments whose device has a non-blank platform id are polled (batches of `BATCH_SIZE` = 50). A device the platform leaves out of its answer gets no new data, so its streams age normally.
  - The poll interval guard allows 30 s of jitter. `poll_all(force=True)` skips it. Each device is applied in its own savepoint; an error on one device is logged and does not stop the others.
  - After 3 consecutive failed polls one `platform_unreachable` notification is sent, and one `platform_reachable` notification when it works again. No device alerts are raised while the platform fails.
  - A stream that has **never** reported is aged from `deployed_at`: it stays UNKNOWN until it is overdue, then LATE / MISSING. NO_CONTACT is likewise measured from `deployed_at` when there was never any contact. So a freshly deployed device without data is UNKNOWN with no alerts, and a device that never reports is flagged.
  - `last_seen` and `last_contact` never move backwards. Contact counts data from any stream the platform reports, including keys InvenTree does not know. Disabled streams are UNKNOWN, not monitored, and their alerts clear.
  - Geofence: the **polygon wins** when set, otherwise the radius circle around the nominal position. The radius is the deployment's, else the site's, else `FLEET_GEOFENCE_DEFAULT_RADIUS_M`; the nominal position falls back to the site. The "degraded near the edge" rule only applies to the radius.
  - Positions: the last position only moves forward in time. A `PositionFix` is stored if it is the first, ≥ 60 min after the last stored fix, or > 25 m from it.
  - Dedupe keys: `dp:<pk>:<TYPE>` and `dp:<pk>:<TYPE>:<stream key>` for stream alerts. LATE and MISSING are separate alerts: when a stream goes from LATE to MISSING the LATE alert resolves (AUTO) and a MISSING alert opens.
  - Notifications go out only for CRITICAL: on open, on escalation to CRITICAL (e.g. a stream marked essential), and when a CRITICAL alert resolves by itself (AUTO). WARNING alerts are not notified.
  - `acknowledge_alert` (OPEN → ACKNOWLEDGED) and `resolve_alert` (MANUAL, optional note in `data.resolution_note`) live in `monitoring.py`. An acknowledged alert still clears automatically. A manually resolved alert whose condition persists is opened again (as a new alert) on the next poll.
  - `verify_now(dep, since=None)` defaults `since` to now minus `device_type.verify_window_minutes`, uses the newer of the live and the cached `last_seen`, and raises a `ValidationError` when the device has no platform id or the platform is unreachable. Only enabled streams count. It stores nothing.
  - `prune_positions()` (2 years) runs from the new daily task `fleet_daily_planning`, which P3 extends with the planning calls.

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
- **As built in P2:** `get_client()` returns the mock unless `FLEET_DATA_PROVIDER` is `http`. Client errors are raised as `DataPlatformError`. The mock (`MockDataPlatformClient`) has class attributes `statuses`, `error`, `generate` and `calls`, plus `reset()`; with `generate=True` (default) a device without a canned status reports every enabled stream as seen now at its nominal position, so a dev server shows healthy devices. The HTTP client **assumes** `GET {url}/devices/status/?ids=a,b` → `{"devices": [{"id", "streams": [{"key", "last_seen"}], "position": {"latitude", "longitude", "timestamp"}}]}`; only `parse_device()` and the request in `get_status()` should change once the real API spec arrives (section 12, question 1).

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

- **As built in P3:**
  - `propose_pm_tasks(today=None)`: DEPLOYED, coverage **FULL**, a device, `next_pm_date <= today + FLEET_PLAN_HORIZON_DAYS`. With an open PREVENTIVE task nothing is created, but the due date of a task that is still PROPOSED follows `next_pm_date`. No site is needed (`site` is copied from the deployment, may be null).
  - `cancel_stale_proposals()`: PROPOSED PREVENTIVE tasks whose deployment is no longer DEPLOYED or no longer FULL coverage are CANCELLED.
  - `pm_alerts(today=None)`: dedupe keys `dp:<pk>:PM_DUE` / `dp:<pk>:PM_OVERDUE`; DEPLOYED, not NO_SERVICE (FULL and THIRD_PARTY get them, as the plan says). Overdue = `next_pm_date < today`. The message names the site nickname, else the serial. Cleared alerts resolve AUTO. Not notified (INFO/WARNING).
  - `pipeline_alerts(today=None)`: one alert per `readiness_risks` code, dedupe `dp:<pk>:<CODE>`; risks that disappear (or deployments that leave the pipeline) resolve AUTO. **BUILD_LATE and NOT_READY are notified once when they open** (`notify` event `pipeline_risk`, Teams + email + in-app). A CRITICAL NOT_READY is already notified by `open_or_update_alert`, so it is not sent twice. PARTS_SHORT and NO_DEPLOY_DATE are raised but not notified.
  - `run_daily_planning(today=None)`: cancel stale proposals, propose, PM alerts, pipeline alerts; each step in its own transaction, a failing step is logged and returns `None` in the summary without stopping the others. `fleet_daily_planning` calls it, then prunes positions.
  - `suggest_kit` is **not** in P3: it needs trips (P4). **Done in P4**, see `services/trips.py` "As built in P4" below.

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
- **As built in P3:**
  - `create_task(device, task_type=CORRECTIVE, deployment=None, site=None, description, due_date, scheduled_date, **kwargs)`: the device must be a single serialized unit. The deployment defaults to the device's DEPLOYED deployment (none = workshop task) and must be for that device; the site defaults to the deployment's. SCHEDULED when a date is given, else PROPOSED. The task API `POST` goes through it.
  - `update_schedule(task)` (after an edit): PROPOSED + scheduled date -> SCHEDULED; SCHEDULED without date and without trip -> PROPOSED. P3 has no `schedule_tasks(trip, …)`: that comes with trips (P4).
  - `start(task, user)`: PROPOSED/SCHEDULED -> IN_PROGRESS, `started_at/by`, the user becomes a technician. The device type is the deployment's, else the one of the device part or its nearest template. The checklist is created once.
  - `component_action(task, user, data)`: IN_PROGRESS only. Calls `change_components` **unchanged**; its DRF errors are re-raised as Django `ValidationError`s. The removal location (remove/replace) defaults to the trip kit's `Removed` child (created on first use, non-structural; P4 creates trip kits) else `FLEET_WORKSHOP_LOCATION`; structural locations are refused. Notes are prefixed `[MT-xxxx]`. New tracking rows (`pk > max` before the call) of the unit, the component, the source and the resulting items get `deltas.maintenance = task.pk`. A `MaintenanceAction` stores `component_out/in`, part, quantity, disposition (`destroyed` for destroy), destination, fault code, checklist result.
  - **Scenario 6:** `component_action` accepts `disable_stream` (a DataStream of the task's deployment; remove or destroy only). The stream becomes disabled and non-essential, its open alerts resolve (TASK), and the action metadata records `disabled_stream`. The UI shows it as "Stream no longer reported" in the remove/destroy dialogs. There is no automatic part-to-stream mapping: the technician picks the stream.
  - `consume(task, user, stock_item, quantity, note)`: bulk (non-serialized) stock in stock, quantity <= available; with a trip kit location the stock must be inside it. Uses `take_stock` (notes `[MT-xxxx] …`), tags the tracking row, records CONSUME (part kept even if the item was deleted when depleted).
  - `reposition(task, user, lat, lon, note)`: DEPLOYED deployment only; uses `deployment.set_position`; old and new position in the action metadata.
  - `record_action(task, user, action, note, fault_code, checklist_result, firmware_version)`: REPAIR / CLEAN / FIRMWARE / OTHER without a stock change (the plan's "Note" action). A FIRMWARE action with a version updates `DeviceLink.firmware_version` (old/new in metadata).
  - `verify(task)`: only when the task's deployment is DEPLOYED; `verify_now(dep, since=task.started_at)`; the result is stored JSON-safe (ISO dates) in `task.verification`.
  - `complete(task, user, labour_minutes, summary, override_reason)`: required checklist items must not be PENDING. **Verification is needed only for a device in the water** (deployment DEPLOYED); workshop tasks and tasks on recovered devices close without it. Without a passed check an override reason is required, stored in `verification_override_reason`, and a PROPOSED CORRECTIVE follow-up (`follow_up_of`) is created. Then: COMPLETED, alerts resolved (TASK, `alert.task` set, linked to the task) = the task's linked alerts + the deployment's open **monitoring** alerts when the check passed (a condition that persists is reopened by the next poll) + the PM alerts for PREVENTIVE/DEPLOYMENT/SWAP tasks; `compute_next_pm`; one `FLEET_MAINTENANCE` (132) tracking entry on the device (`deltas: maintenance, deployment, task_type`); notification `task_completed` (Teams card text = summary + one line per action, e.g. "Replace: Battery pack #BAT-0331 -> Battery pack #BAT-0347").
  - `cancel(task, user, reason)`: any open task; stock changes stay; the reason is appended to the summary.
  - `create_from_alert(alert, user, scheduled_date, trip, description)`: unresolved alert with a deployment and device, and no **open** task already; CORRECTIVE, description defaults to the alert message, alert linked (`task.alerts` and `alert.task`) and acknowledged.

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

**As built in P4** (`services/trips.py`, and additions to `planning`, `maintenance`, `deployment` and `pipeline`; nothing needs a site or a position):
- **Kit locations.**
  - `create_trip(title, start_date, end_date, vessel, responsible, team, tasks, deployments, **kwargs)` creates the trip, then `ensure_kit_location()`.
    - The location is a non-structural `StockLocation` named after the reference (`TRIP-0001`), under `FLEET_KIT_PARENT_LOCATION`, or **at the top level when that setting is empty**.
    - It also gets its non-structural `Removed` child; the name is `maintenance.REMOVED_LOCATION_NAME`, the same location `maintenance.get_removed_location()` uses.
  - Tasks and ready deployments can be added at once; this is how plan selection creates a trip. The end date cannot be before the start.
  - `kit_stock(trip)` is the in-stock items in the kit, excluding `Removed`. `removed_stock(trip)` is what is in `Removed`, any status.
  - `kit_locations()` returns every kit location: the kit parent's tree plus every trip's kit tree. It is used for availability.
- **Scheduling.**
  - `trips.add_tasks(trip, tasks, deployments)` works on a trip that is PLANNING, KIT_READY or IN_PROGRESS.
  - `maintenance.schedule_tasks(trip, tasks)` takes PROPOSED or SCHEDULED tasks only. A task on another **open** trip is refused.
    - The task becomes SCHEDULED with the trip set.
    - The scheduled date is kept if it falls within the trip dates; otherwise it becomes the trip start date.
    - A DEPLOYMENT or SWAP task of a pipeline deployment goes through `deployment.schedule()` instead.
  - `deployment.schedule(dep, trip)` takes a READY or SCHEDULED deployment that has a device.
    - It reuses the deployment's open DEPLOYMENT/SWAP task, or creates one. The task is **SWAP when `replaces` is DEPLOYED**, otherwise DEPLOYMENT.
    - The task description is "Deploy DP-xxxx (site)" or "Swap: DP-old -> DP-new (site)". The due date is the target date; the scheduled date is the trip start.
    - The deployment becomes SCHEDULED, and the device becomes a `DEVICE` kit line linked to the task.
  - `trips.remove_task(trip, task)` calls `maintenance.unschedule_task(task)`, which only works on tasks that have not started.
    - The task becomes PROPOSED again, with no trip and no date, and its kit lines are deleted.
    - A deployment task's deployment becomes READY again (`deployment.unschedule`).
  - `pipeline.assign_existing_device` on a SCHEDULED deployment moves the open deployment task, and its kit line, to the new unit.
  - `create_from_alert(..., trip=)` schedules the new task on the trip. The alert → task API accepts `trip` again.
- **Kit suggestion: `planning.suggest_kit(trip, apply=True)`.** It goes through the trip's open tasks:
  - **Deployment or swap task of a pipeline deployment:** only a `DEVICE` line for the device, with the deployment reference as the note. Template lines are skipped: they are the PM kit, which a new unit does not need.
  - **Other tasks:** the device type's `KitTemplateLine`s, as `ALWAYS` / `LIKELY`. Quantities of the same part and source **add up per task**, and the note lists the site nicknames or serials.
  - **Failure history, per device type:** a part with a replace / remove / destroy action that has a fault code in at least `FAILURE_MIN_TASKS` = 2 of the last `FAILURE_HISTORY_TASKS` = 10 completed tasks of that type becomes `LIKELY` with quantity 1 and the note "Failed in 2/10 recent tasks". If the template already has a LIKELY line for that part, only the note is added.
  - It replaces every line **except MANUAL** (`trips.apply_kit_suggestion`).
  - `trips.kit_lines(trip)` adds availability to each line:
    - `available`: in-stock quantity of the part outside every kit location. For a device line, 1 while the device is in stock.
    - `in_kit` and `missing`: planned quantity minus what is in the kit.
- **Kit preparation: `prepare_kit(trip, items, user)`.**
  - Each item is `{stock_item, quantity?}`, moved with `StockItem.move()` (STOCK_MOVE). A partial quantity is split off.
  - It is refused for stock that is not in stock, already in the kit, over the quantity, or serialized and not taken whole.
  - Allowed while PLANNING, KIT_READY or IN_PROGRESS.
  - The first preparation moves PLANNING → KIT_READY and notifies `trip_kit_ready`: Teams, email and in-app, with title "TRIP-xxxx ready - title" and portal link `trips/<pk>`.
- **Start: `start_trip`.** PLANNING or KIT_READY → IN_PROGRESS. The responsible person defaults to the user who starts the trip.
- **Deploy and swap from a task: `maintenance.deploy(task, user, latitude, longitude, depth_m, deployed_at, note)`.**
  - Only for an IN_PROGRESS DEPLOYMENT or SWAP task. It calls `deployment.deploy()`, which gained `recover_location`.
  - In a swap, the replaced device is recovered into the trip's `Removed` location, or the workshop when there is no trip.
  - It records an `OTHER` action: `component_in` is the new device, `component_out` the recovered one, and the metadata holds `deployed`, `recovered`, `recovered_device` and `recovered_location`.
  - Completing the task restarts the PM interval as before (DEPLOYMENT and SWAP are PM base types).
- **Reconcile: `reconcile(trip, user, returns)`.**
  - Allowed while KIT_READY, IN_PROGRESS or RECONCILING. It is **refused while a task is IN_PROGRESS**: complete or cancel it first. The plan's "back to PROPOSED" would have thrown away work in progress.
  - The status becomes RECONCILING.
  - Each leftover kit item goes to `returns[item]`, else the part's `get_default_location()` (the part, then its category).
  - Each removed item goes to `returns[item]`, else `FLEET_WORKSHOP_LOCATION`. It keeps its status: a DAMAGED item is not "in stock", so `move_removed()` moves it directly and writes the same STOCK_MOVE entry.
  - Tasks not started are released, as in `remove_task`.
  - The trip is **CLOSED** only when nothing is left without a destination and no task is open. The end date defaults to today. Otherwise it stays RECONCILING, and the result lists `missing_location` and `open_tasks`.
  - Result: `{returned, to_workshop, released, missing_location, open_tasks, closed}`.
- **Cancel: `cancel_trip`.** Only for PLANNING or KIT_READY trips with an empty kit: return the stock first. It releases the tasks and appends the reason to the notes.
- **Delete:** `delete_kit_locations(trip)` removes the empty kit locations when a trip is deleted.

**`notify.py`**
- `notify(event_code, title, text, link, severity, obj=None)` sends to three channels:
  1. **Teams:** POST an Adaptive Card to `FLEET_TEAMS_WEBHOOK_URL` through `offload_task`. Include the title, a fact set and an "Open" button linking to `FLEET_PORTAL_URL` + the path. A failure is logged and not raised.
  2. **Email:** `InvenTree.helpers_email.send_email(subject, body, recipients=FLEET_ALERT_EMAILS, html_message=…)`, only when `is_email_configured()`.
  3. **In-app:** `common.notifications.trigger_notification(obj, category=f'fleet.{event_code}', targets=<users with fleet.view role>)`.
     - This uses InvenTree's **built-in** UI notification mechanism. It is part of core, not a custom plugin, and is acceptable.
     - If it pulls in plugin machinery that is disabled in production, skip this channel.
- **As built in P2:** `notify()` never raises; each channel runs in its own savepoint and failures are logged. Teams uses the Workflows message format (`type: message` with an Adaptive Card 1.4 attachment: coloured title, text, fact set, "Open" button) and is posted through `offload_task(post_teams_card, …)`. Links use `FLEET_PORTAL_URL` + a portal path when set, else the object's page in the main UI. In-app notifications use only the built-in `inventree-ui-notification` method (`check_recent=False`), are sent to active users with the fleet view role and superusers, and are **skipped when there is no object** (InvenTree stores each notification against an object id; e.g. "platform unreachable" goes to Teams and email only).
- Events notified:
  - alert opened or escalated (CRITICAL);
  - BUILD_LATE / NOT_READY;
  - trip kit ready;
  - task completed;
  - data platform unreachable.

### 2.7 Scheduled tasks (`fleet/tasks.py`)
| Function | Schedule | Calls |
|---|---|---|
| `fleet_poll` (**done in P2**) | `@scheduled_task(ScheduledTask.MINUTES, 1)` | `monitoring.poll_all()`, which applies its own interval guard |
| `fleet_sync_pipeline` (**done in P1**, extended in **P2b**) | MINUTES 10 | `pipeline.sync_all()`: creates missed PLANNED deployments of pending sales build orders (P2b), then `sync_build_output` for deployments with status IN_PRODUCTION or PLANNED that have a build; then `stock_sync.sync_stock_deployments()` (P2b) |
| `fleet_daily_planning` (prune **done in P2**, planning **done in P3**) | DAILY | `planning.run_daily_planning()` (cancel stale proposals, `propose_pm_tasks`, `pm_alerts`, `pipeline_alerts`), then prune `PositionFix` older than 2 years |

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

**As built in P1** (`fleet/api.py`; anything not listed is still to do):
- `device-type/` + `<pk>/` (FleetDeviceType CRUD; filters `part`, `active`; annotated `stream_count`, `deployment_count`; delete refused while it has deployments).
- Stream templates are **flat**, not nested: `device-type/stream-template/` + `<pk>/`, filtered with `?device_type=` (names `api-fleet-stream-template-list|detail`). P3 should use the same layout for `device-type/checklist-item/` and `device-type/kit-line/`.
- `site/` + `<pk>/` (filters `coverage`, `active`, `client`, `has_active_deployment`; annotated `active_deployment`, `active_deployment_reference`, `deployment_count`; delete refused while it has deployments).
- `device-link/` + `<pk>/` (filter `stock_item`; platform id unique when set).
- `deployment/` + `<pk>/`:
  - Filters: `status` (incl. custom keys), `pipeline`, `active`, `closed`, `unscheduled`, `has_device`, `pm_due_before`, `target_before`, `target_after`, `has_open_alerts`, `site`, `health`, `coverage`, `client`, `device`, `build`, `device_type`, `deployment_type`.
  - Output options: `device_type_detail`, `build_detail`, `device_detail`, `site_detail` (default on), `client_detail`, `risks` (default off).
  - Extra read-only fields: `platform_id`, `replaces_reference`, `open_alert_count`, `metadata_warning`, `creation_date`.
  - `status` and the live-state fields are read-only. POST always creates a PLANNED deployment and copies the coverage from the site unless `coverage` is sent.
  - Delete is allowed only for PLANNED or CANCELLED deployments.
- Actions `deployment/<pk>/create-build|assign-device|deploy|recover/`: POST, returning the updated deployment (200).
  - Required roles: `create-build` needs **fleet.add + build.add**; `assign-device` needs fleet.change; `deploy` and `recover` need **fleet.change + stock.change** (checked explicitly, 403 otherwise).
  - These views use `FleetRoleMixin` (`IsAuthenticated`, `RolePermission`, token scopes; `role_required='fleet'`, `rolemap={'POST': 'change'}`). The default `ModelPermission` would require `add_deployment` for any POST.
- `calendar/?start&end`: returns `[{model_type, pk, title, start, end, status, url}]` for pipeline deployments (target date), open tasks (scheduled or due date) and trips. `model_type` is `deployment`, `maintenancetask` or `fieldtrip`.
- `overview/`: returns `deployed`, `planned`, `in_production`, `ready`, `scheduled`, `to_deploy_30`, `unscheduled`, `pm_due_30`, `pm_overdue`, and (P2) `health_ok|degraded|critical|unknown` (deployed only), `open_alerts`, `alerts_critical|warning|info`.

**As built in P2:**
- Streams are **flat**, like stream templates: `deployment/stream/` + `<pk>/` (names `api-fleet-stream-list|detail`), filtered with `?deployment=` (also `enabled`, `essential`, `state`). Standard model permissions (fleet role). `last_seen` and `state` are read-only; `deployment` and `key` cannot change after creation. This replaces `deployment/<pk>/streams/`.
- `deployment/<pk>/track/` (GET, fleet view): position fixes oldest first; `?since=` (ISO datetime, 400 if invalid).
- `deployment/map/` (GET, fleet view): DEPLOYED deployments only; filters `site`, `deployment`, `health`. Each row has `latitude/longitude` (last position, else nominal), `nominal_*`, `last_*`, `last_position_at`, `distance_from_nominal_m`, the effective `radius_m` and `polygon`, `health`, `last_contact`, `open_alert_count`, site and device serial.
- `deployment/<pk>/verify/` (POST `{since?}`, fleet change): returns the `verify_now` result; 400 unless the deployment is DEPLOYED, has a platform id and the platform answers.
- `alert/` + `<pk>/` are **read-only** (alerts are raised by the system). Filters `status`, `open`, `severity`, `alert_type`, `deployment`, `site`, `stream`, `device`, `resolution`, `opened_after`, `opened_before`; ordering by `severity` uses a rank (CRITICAL > WARNING > INFO); default `-opened_at`. Output options `deployment_detail`, `site_detail` (default on); extra fields `stream_key`, `device_serial`, `acknowledged_by_name`, `resolved_by_name`.
- `alert/<pk>/acknowledge/` and `alert/<pk>/resolve/` `{note?}`: POST, fleet change, return the alert; 400 when already resolved.
- Not done yet: `alert/<pk>/create-task/` (P3, needs the maintenance service), task (P3), trip (P4) and fault-code (P3) endpoints.

**As built in P2b:**
- `deployment/<pk>/set-position/` (POST `{latitude, longitude, depth_m?, geofence_radius_m?}`, fleet change, name `api-fleet-deployment-set-position`) returns the deployment. It answers 400 for closed deployments or invalid coordinates.
- `deploy/` accepts a deployment without a site (see 2.6).
- Deployment filters `has_site` and `has_position` (latitude and longitude set). Search also matches `client__name`.
- Editing a deployment (PATCH): setting a site on a **DEPLOYED** deployment copies the site position (and depth) only when the deployment has no position; an existing position is never overwritten. A site that already has another DEPLOYED deployment is refused (400). Pipeline deployments take the site position when deployed, so changing a planned site still moves the position.
- `overview/` gained `no_position` and `no_site` (DEPLOYED only).
- `deployment/map/` is unchanged: rows without any position have `latitude/longitude = null`, and the frontend counts them.

**As built in P3:**
- Flat device-type templates, like stream templates: `device-type/checklist-item/` + `<pk>/` (filters `device_type`, `task_type`, `kind`, `required`; ordered by `sequence`) and `device-type/kit-line/` + `<pk>/` (filters `device_type`, `mode`, `part`; `part_detail`; quantity >= 0). Standard model permissions. The device type serializer gained `checklist_count` and `kit_line_count`.
- `fault-code/` + `<pk>/` (filters `category`, `active`); create/edit/delete with the fleet add/change/delete roles (mark codes inactive instead of deleting them; inactive codes are refused in checklists and actions).
- `task/` + `<pk>/`: `POST` creates through `create_task` (fleet **add**: managers); `PATCH` keeps the status in step with `scheduled_date`; `task_type`, `device` and `deployment` cannot change once started; `DELETE` only PROPOSED/CANCELLED tasks without actions. Read-only: status, trip, started/completed, verification, override reason, follow-up, alerts. Extra fields: `display_name` (site nickname, else serial), `overdue`, `trip_reference`, `started_by_name`, `completed_by_name`, `technician_names`, `follow_up_of_reference`, `checklist_count`, `checklist_pending` (required + PENDING), `action_count`. Output options `device_detail`, `deployment_detail`, `site_detail` (default on).
- Task filters: `status` (incl. custom keys), `open`, `overdue`, `task_type`, `site`, `trip`, `has_trip`, `device`, `deployment`, `follow_up_of`, `due_before`, `due_after`, `assigned_to_me` (technician, started by, trip team or trip responsible), `min_date`/`max_date` (scheduled date, else due date: used by the calendar). Search: reference, description, summary, site, deployment, serial, part.
- Actions (POST, fleet **change**, return the task, 200): `task/<pk>/start|component-action|consume|reposition|record-action|verify|complete|cancel/` (names `api-fleet-task-<slug>`). `component-action` and `consume` also need **stock.change** (403 otherwise). `component-action` takes the body of `/api/stock/<pk>/components/` (its serializer is subclassed) with an optional `location`, plus `fault_code`, `checklist_result`, `disable_stream`. `record-action` (not in the original list) is the "Note" action. `verify` stores the result on the task instead of returning it separately.
- The checklist and actions are **flat**: `task/checklist/` + `<pk>/` (list + retrieve/PATCH `result`, `value`, `note`, `fault_code`; only while the task is IN_PROGRESS; fleet change; `kind`/`unit` come from the template item) and `task/action/` (read-only list, `?task=`). They replace `task/<pk>/checklist/` and `task/<pk>/actions/`.
- `alert/<pk>/create-task/` (POST `{scheduled_date?, description?}`, fleet change, 201, returns the task). `trip` is left out until P4: `FieldTrip.get_api_url()` reverses `api-fleet-trip-list`, which does not exist yet, and a writable `trip` field would break the form's OPTIONS call.
- Alert filter `task`. Deployment filter `pm_overdue` (DEPLOYED, `next_pm_date < today`). `overview/` gained `tasks_open`, `tasks_in_progress`, `tasks_overdue`. The calendar already listed open tasks (P1).

**As built in P4:**
- **`trip/` and `trip/<pk>/`.**
  - Fields: reference, status, title, start/end date, vessel, `team` (user pks), `team_names`, `responsible`, `responsible_name`, `kit_location` (read-only), `kit_location_name`, `removed_location`, `task_count`, `open_task_count`, `completed_task_count`, `kit_line_count`, notes.
  - Write-only on create: `tasks` and `deployments`, for plan selection. On edit they are refused (use `add-tasks`).
  - POST goes through `trips.create_trip` and needs fleet **add** (managers).
  - DELETE only for PLANNING or CANCELLED trips without open tasks; it also deletes the empty kit locations.
  - Filters: `status`, `open`, `mine` (team or responsible), `responsible`, `team`, `start_after`, `start_before`, `min_date`, `max_date`. Default ordering `-start_date`.
- **`trip/kit-line/` and `<pk>/`**, flat like the other children (names `api-fleet-trip-kit-line-list|detail`).
  - Lines created through the API are MANUAL; `source`, `stock_item` and `task` are read-only.
  - The kit of a closed trip cannot change. Filters: `trip`, `part`, `source`, `task`, `stock_item`.
- **`trip/<pk>/kit/`** (GET, fleet view) returns `{trip, kit_location, removed_location, lines: [{line, available, in_kit, missing}], contents: [stock], removed: [stock]}`.
- **Trip actions** (POST, fleet **change**, return the trip with 200): `trip/<pk>/add-tasks|remove-task|suggest-kit|prepare-kit|start|reconcile|cancel/`, names `api-fleet-trip-<slug>`.
  - `prepare-kit` and `reconcile` also need **stock.change**.
  - `reconcile` adds `reconcile: {…result…}` to the response.
  - `remove-task` is not in the original list; the trip page needs it.
- **`task/<pk>/deploy/`** (POST `{latitude?, longitude?, depth_m?, deployed_at?, note?}`, fleet change + stock change) deploys from a DEPLOYMENT or SWAP task.
- **`alert/<pk>/create-task/`** accepts `trip` again. `FieldTrip.get_api_url()` now resolves, so the writable field no longer breaks the OPTIONS call.
- The calendar already listed trips (P1).

### 2.9 Admin, reports, data migration
- Register every model in `admin.py`, using the InvenTree admin patterns.
- Reports: `Deployment`, `MaintenanceTask` (service report) and `FieldTrip` (trip report) get a `report_context()` with a typed return. Add default templates under `fleet/templates/fleet/` and document how to upload them as report templates.
  - **Done in P3 (service report):** `fleet/templates/fleet/fleet_service_report.html` (details, checklist, actions and parts, data check, alerts, summary, notes). Install it with **`python manage.py fleet_install_reports`** (creates the report template "Fleet Service Report", model type `maintenancetask`, file name `ServiceReport-{{ reference }}.pdf`; `--update` replaces the file of an existing one). The core `report` app is not changed. It then appears in the print menu of the task page. Run the command once after deploying P3 (add it to the P6 runbook).
  - **Done in P4 (trip report):** `fleet/templates/fleet/fleet_trip_report.html`, covering details and team, tasks with their data check and summary, actions, parts used, planned kit, alerts resolved and notes. The same command installs it as "Fleet Trip Report" (model type `fieldtrip`, `TripReport-{{ reference }}.pdf`). **Run `fleet_install_reports` again after deploying P4**: it creates the missing trip report and keeps the existing service report.
- ~~Add a management command `fleet_import_existing` for a one-time import of today's deployed systems~~ **Superseded (2026-10-08):** the product owner does not want an import. P2b replaced it with the automatic stock sync (section 2.10) and **removed** the command and `test_import.py` (done). Kept below for history:
  - For each stock item with a `customer`, whose part is a fleet device type, create a Site (named after the customer or the stock notes; the manager edits it later) and a DEPLOYED Deployment.
  - `deployed_at` = the last 60/100 tracking date (same logic as the plugin's `deployed_api`).
  - Add a dry-run flag, and run it with the product owner.
  - **Done in P1** (`python manage.py fleet_import_existing [--dry-run]`). How it behaves:
    - The site name is the first line of the stock item notes, else `"<customer> <serial>"`.
    - `site.client` is the customer, unless it is `FLEET_DEFAULT_CUSTOMER`.
    - It matches the device-type part and its variants, and copies the streams.
    - It creates the DeviceLink (blank platform id) and computes the next PM.
    - It writes **no** stock tracking entries.
    - It skips devices that already have a DEPLOYED deployment, so re-running it is safe.
    - It has **not** been run against real data.

### 2.10 Stock-driven deployments (phase P2b)
Decided 2026-10-08 (section 0.2). The stock list is the source of truth for which devices are deployed; the fleet app follows it automatically.

**Who counts as deployed:** a serialized `StockItem` (quantity 1) whose part is the part of an **active** `FleetDeviceType`, or a variant of it, and whose `customer` is set. Items of other parts are ignored, even with a customer.

**`services/stock_sync.py`** (new), `sync_stock_deployments(dry_run=False) -> summary`:
1. **Open:** for each deployed device (above) without a DEPLOYED deployment:
   - if the device has a pipeline deployment (PLANNED to SCHEDULED, e.g. its build order deployment), move **that** deployment to DEPLOYED instead of creating a new one;
   - otherwise create `Deployment(status=DEPLOYED, device_type, device, site=None, client=customer, coverage=FULL, deployment_type=NEW_STATION)` with **no position**;
   - `deployed_at` = the date of the last SENT_TO_CUSTOMER (100) / SHIPPED_AGAINST_SALES_ORDER tracking entry, else now (same logic as the legacy `deployed_api` and the old import command; reuse that code);
   - copy the stream templates, `ensure_device_link()` (blank platform id = "not configured"), `compute_next_pm()`;
   - write **no** stock tracking entries: the customer assignment is already in the stock history.
2. **Close:** a DEPLOYED deployment whose device no longer has a customer (returned, or the item was deleted) becomes RECOVERED with `recovered_at` = now (or the return tracking date), and its open alerts are resolved (AUTO). No stock movement: it has already happened.
3. **Customer changed:** update `client` on the DEPLOYED deployment.
4. Idempotent; each device in its own savepoint; errors are logged per device.

**Triggers:**
- Extend the scheduled task `fleet_sync_pipeline` (every 10 minutes) to also call `sync_stock_deployments()`. Customer changes often happen through bulk operations or sales order shipping, so `post_save` is not reliable (same reason as build outputs).
- The Fleet `deploy`/`recover` actions already update both the stock item and the deployment, so they need no extra call.
- Management command `fleet_sync_stock [--dry-run]` prints what would be opened, closed or changed, for a check before the first real run on production.

**Site optional, position per device:**
- `deploy()` and the deploy form: the site is optional (see 2.6).
- Deployments without a position: no geofence check, not drawn on the map; the map panel says how many devices have no position. Monitoring of data streams works normally.
- Add a **Set position** action on the deployment (latitude, longitude, depth, optional geofence radius) for fleet.change; editing the deployment can also set the site later. Setting a site does not overwrite a position that is already set.
- Deployment list: a *Position* column ("Not set" badge) and filters `has_position`, `has_site`; overview KPIs `no_position`, `no_site`.
- P3 planning must not need a site: the PM interval comes from the device type unless a site override exists; tasks show the site nickname when there is one, else the device serial.

**Sales visibility (open question 8, confirm before building this part):** "every device of the fleet type that is in a build order of a sale must be visible in the Fleet list". What already works: a fleet build order gets a deployment when it is **issued**, whether or not it is linked to a sales order, and once the sales order ships the device (customer set) the sync above marks it DEPLOYED. Candidate additions, to confirm:
- (a) show the sales order and its customer on pipeline deployments whose build order has `sales_order` set (column + filter `sales_order`), and copy the customer into `client`;
- (b) also list fleet build orders that are still **PENDING** (not issued) when they are linked to a sales order — this changes the 0.2 rule "pipeline starts at issue" for sales builds only;
- (c) also list fleet devices already built and **allocated to a sales order shipment** but not shipped yet.

**Tests (P2b):** sync opens a deployment for a fleet item given a customer by hand and by sales order shipment; ignores non-fleet parts and non-serialized items; reuses a READY pipeline deployment; closes on return; updates the client on customer change; dry run changes nothing; idempotent on re-run; deploy without a site; monitoring of a device without a position (no geofence alert); `fleet_import_existing` and `test_import.py` removed.

**Answer to question 8 (2026-10-08): option (b) only.**

**As built in P2b:**
- `services/stock_sync.py`: `sync_stock_deployments(dry_run=False)` returns `{opened, closed, updated, errors}`, each a list of one-line descriptions.
  - The whole run is one transaction (rolled back for a dry run), and each device change is its own savepoint. An error is logged and listed without stopping the others. Order: close, update, open.
  - **Fleet parts:** every part of an active `FleetDeviceType`, and its variants (`get_descendants(include_self=True)`). A variant with its own active device type uses that one. Inactive device types are ignored.
  - **Deployed items:** `customer` set, `serial` not blank, `quantity == 1`, not `is_building`.
  - **Open:** the device's pipeline deployment (PLANNED to SCHEDULED, highest status first) is reused; otherwise a new NEW_STATION deployment is created, with no site and no position.
    - `client` = the customer; `deployed_at` = the last 60/100 tracking date, else now.
    - A reused deployment with a site copies the site position when it has none.
    - **If its site already has another DEPLOYED deployment** (e.g. a replacement unit shipped before the old one was recovered), the site is dropped and `metadata.warning` explains why. The deployment page shows it as a warning.
    - Streams are copied, `ensure_device_link()` and `compute_next_pm()` are called, and no stock tracking is written.
  - **Close:** a DEPLOYED deployment becomes RECOVERED when its device has no customer, or when its stock item was deleted (`device` is null). This covers a manual return and a **Return Order** (receiving a line clears the customer, tracking code 80). The deployment stays in the history as RECOVERED. `recovered_at` = the last RETURNED_FROM_CUSTOMER (105) or RETURNED_AGAINST_RETURN_ORDER (80) date since `deployed_at`, else now. Open alerts are resolved (AUTO). No stock movement.
  - **Update:** `client` follows `device.customer`.
- The `last_deployed_date` logic of the old import command lives on as `stock_sync.last_tracking_date()`.
- `fleet_sync_pipeline` runs the sync every 10 minutes and logs the counts.
- `python manage.py fleet_sync_stock [--dry-run]` prints one `Open:` / `Close:` / `Update:` / `Error:` line per change, then a count line (`Dry run: … Nothing was saved.` or `Done: …`). **Run it with `--dry-run` on production before the worker first runs the new code**, because the scheduled task applies the changes by itself.
- Monitoring needed no change. Without a nominal position (none on the deployment or its site), `geo.check_geofence` returns `inside=None`, so there is no geofence alert and no degraded state. Streams, contact and the last reported position work normally. A geofence **polygon** still applies without a position; site-less devices have none unless one is set in the admin.
- **Sales visibility (b):**
  - `pipeline.on_build_saved` creates a **PLANNED** deployment (no site, no date, linked to the build) for a **PENDING** build order of an active device-type part (exact part, as at issue) when `build.sales_order` is set. This also happens when the sales order is linked later.
  - Issuing the BO moves that same deployment to IN_PRODUCTION (existing P1 rule), and cancelling the BO cancels it.
  - `sync_all()` (via `sync_sales_builds()`) catches up pending sales BOs that have no deployment.
  - When the output ships against the sales order, the stock sync moves the same deployment to DEPLOYED. So a sold device keeps one deployment from BO creation until it reaches the customer.
  - **Not handled:** if the sales order is unlinked from a still-pending BO, the PLANNED deployment stays (the same state that *Create build order* produces). The manager can delete it.
- Frontend: see section 4, "Done in P2b".

### 2.11 Internal device states (phase P7)
Decided 2026-10-09 (question 9). Each device shows **one** internal state in the Fleet lists, next to its deployment status. Three states are **set by a person** (technicians and managers: fleet **change**); four are **worked out automatically**. The first match in this order wins:

| # | State | Set by | Meaning | Effect |
|---|---|---|---|---|
| 1 | `DECOMMISSIONED` | person | Will not become active again | Its DEPLOYED deployment is **closed** (RECOVERED, alerts resolved AUTO, **no stock movement**: the item keeps its customer); tasks that have not started are taken off their trip and CANCELLED. The stock sync **never reopens or recreates** it while the state is set (and closes a DEPLOYED deployment of it if one appears). Refused for a device in a pipeline deployment or with a task IN_PROGRESS. Deploy, assign device and trip scheduling refuse it. Hidden from the active lists, map, monitoring, planning and KPIs; the deployment history shows it with the badge and the filter `decommissioned`. Clearing it lets the sync reopen the device on its next run if it still has a customer. |
| 2 | `DOCKED` | person | Back on land, still the customer's (deployment stays DEPLOYED) | Not polled, open alerts of the deployment resolved (AUTO, not notified), health UNKNOWN; no PM proposals (a still PROPOSED PM task is CANCELLED) and no PM alerts; not on the map; not in the overview KPIs (own tile). Stays in the lists with the badge. A task on a docked device closes without "Verify data". Only for a DEPLOYED deployment. |
| 3 | `MAINTENANCE_SCHEDULED` | automatic | The device has an open task **on an open field trip** | Display only. A task with only a date does not count. |
| 4 | `MAINTENANCE_OVERDUE` | automatic | Routine maintenance is past due: DEPLOYED, coverage not NO_SERVICE, `next_pm_date < today` (same rule as the PM_OVERDUE alert) | Display only; never shown while MAINTENANCE_SCHEDULED. |
| 5 | `PROBLEM_ACKNOWLEDGED` | person | "We know the problem and are working out how to solve it" | Setting it acknowledges the open alerts. Monitoring and alerts go on and stay visible, but **no Teams / email / in-app notifications** for this device (alert opened, escalated, auto-resolved). Clears by hand, or automatically when the device becomes healthy again (health goes from not-OK to OK in a poll) or a task on the device is completed. Only for a DEPLOYED deployment. |
| 6 | `UNRESPONSIVE` | automatic | DEPLOYED, has a platform id, and no stream has reported for `FLEET_NO_CONTACT_HOURS` (from the last contact, else the deploy date) | Display only (the NO_CONTACT alert is unchanged). Back to ACTIVE as soon as data arrives. A device without a platform id is not judged (it is not polled; the page shows "Not configured"). |
| 7 | `ACTIVE` | automatic | DEPLOYED and none of the above | - |

Pipeline and closed deployments have no state (`null`), except DECOMMISSIONED, which shows on every deployment of the device.

**Where it is stored:** the manual state belongs to the **physical device**, on `DeviceLink` (one per stock item, outlives deployments and the stock sync). Migration `0006_p7_device_state`, four `AddField`s on `DeviceLink`, no new table (ruleset unchanged):
- `state` (choices `''` none, `PROBLEM_ACKNOWLEDGED`, `DOCKED`, `DECOMMISSIONED`; blank default)
- `state_note` (250), `state_changed_at` (datetime, null), `state_changed_by` (FK User, null, SET_NULL)

Recovering a deployment (by hand or by the stock sync) clears DOCKED and PROBLEM_ACKNOWLEDGED; DECOMMISSIONED stays.

**The effective state** is one SQL `Case` annotation (`services/device_state.annotate_device_state()`), used by the deployment serializer (`device_state`), the filters, the overview and the map, so every list agrees.

**Service** `services/device_state.py`: `set_state(deployment, state, user, note)` (rules above), `clear_on_close(device)`, `clear_problem(link, reason)`, `is_muted(deployment)`, query helpers `hidden_q()` (DOCKED or DECOMMISSIONED) for the polling, planning, overview and map querysets.

**API:**
- `deployment/<pk>/set-state/` POST `{state, note?}` (fleet change, `state` `''` clears), returns the deployment.
- Deployment fields `device_state` (read-only), `internal_state`, `internal_state_note`, `internal_state_changed_at`, `internal_state_changed_by_name`; filters `device_state` and `decommissioned`.
- Device link fields `state`, `state_note`, `state_changed_at` (read-only; set through the action), filter `state`.
- `overview/`: the deployed counts (deployed, health buckets, PM due/overdue, no position, no site) leave out DOCKED devices; new counts `state_<state>` for each state (decommissioned = device links with that state).
- `deployment/map/` leaves out DOCKED devices and gains `device_state`.

**UI (both):** `DeviceStateBadge` and `deviceStateChoices` in `FleetBadges.tsx`; a *State* column and a *Device State* filter in `DeploymentTable` (plus *Decommissioned* in the history mode); the action **Set Device State** in `useDeploymentActions` (`F/src/fleet/hooks/DeploymentActions.tsx`), used by the main deployment page and the portal device card, with the badge in their headers; state tiles in `OverviewKpis`.

**As built in P7:**
- `fleet/models.py`: `InternalState` (stored, blank = none) and `DeviceState` (effective, in priority order) `TextChoices`; the four `DeviceLink` fields. Migration `0006_p7_device_state` (four `AddField`s, hand-written, confirmed by `makemigrations --check`; reversible).
- `services/device_state.py`: `annotate_device_state(qs, today, now)` (one `Case`: link state, `Exists` open task on an open trip, `next_pm_date < today` and not NO_SERVICE, platform id set and `Coalesce(last_contact, deployed_at)` older than `FLEET_NO_CONTACT_HOURS`); `device_state(dep)`; `set_state()`; `close_decommissioned()` (RECOVERED, `metadata.decommissioned`, alerts resolved, no stock change); `clear_on_close()`, `clear_problem()`, `is_muted()`, `is_docked()`, `is_decommissioned()`, `check_not_decommissioned()`, `hidden_q()`, `decommissioned_q()`. The other services are imported inside `set_state()` (they import this module).
- **Stock sync:** the close step also takes DEPLOYED deployments of decommissioned devices (summary line ends with `(decommissioned)`, closed with `close_decommissioned`); the open step excludes decommissioned devices; a normal close clears DOCKED / PROBLEM_ACKNOWLEDGED. Dry run behaves the same.
- **Monitoring:** `monitored_deployments()` excludes docked and decommissioned devices. `open_or_update_alert` (opened, escalated) and `resolve_alert` (AUTO resolution card) skip the notification while `is_muted()`. `apply_status` clears PROBLEM_ACKNOWLEDGED when the health goes from not-OK to OK (after the alerts of that poll, so their resolution is not notified either).
- **Planning:** `pm_deployments()` and `pm_alerts()` exclude docked/decommissioned devices; `cancel_stale_proposals()` also cancels the PROPOSED PM tasks of docked devices (e.g. docked in the admin).
- **Deployment / pipeline / maintenance:** `deploy()`, `schedule()` and `assign_existing_device()` refuse a decommissioned device; `recover()` clears DOCKED / PROBLEM_ACKNOWLEDGED; `needs_verification()` is false for a docked device (also in `TaskExecution`, which hides *Verify data* and the live streams); `complete()` clears PROBLEM_ACKNOWLEDGED (note "Cleared: task completed (MT-xxxx)").
- **API:** `deployment/<pk>/set-state/` (`api-fleet-deployment-set-state`, fleet change; `{state: '' | PROBLEM_ACKNOWLEDGED | DOCKED | DECOMMISSIONED, note}`; 400 for an invalid state, a device not deployed (problem/docked), a pipeline device or a task in progress (decommissioned), or no device). Deployment fields `device_state`, `internal_state`, `internal_state_note`, `internal_state_changed_at`, `internal_state_changed_by_name`; ordering `device_state`; filters `device_state`, `decommissioned`. The brief deployment (in tasks and alerts) has `internal_state`. Device link fields `state`, `state_note`, `state_changed_at` (read-only), `state_changed_by_name`; filter `state`. Overview: deployed counts without docked devices, plus `state_<state>` for the seven states. Map: docked left out, `device_state` per row. Admin: device link list shows and filters the state.
- **Frontend:** as listed above; the form offers *Problem Acknowledged* and *Docked* only for a deployed device; the deployment pages show an alert with the state, its note, who and when. The *Set Device State* action is in the main UI options menu and is a *Set State* button on the portal device card. A device decommissioned before it ever had a deployment can only be cleared in the admin (it has no deployment page).
- `D/docs/13-fleet.md`: the states in *Key ideas*, a *Device states* section for managers, docked tasks without the data check.

### 2.12 InvenTree conventions pass (phase P8, 2026-10-09)
A review compared the module with the core apps (build, order, stock) and aligned it with InvenTree's way of doing things. Behaviour is unchanged unless noted.

**Models (migration `0007_inventree_conventions`, reversible, tested in `test_migrations.py`):**
- **State transitions on the models**, as `Build.issue_build()`: `Deployment`, `MaintenanceTask`, `FieldTrip` and `Alert` now mix in `StateTransitionMixin` and have `can_*` properties plus transition methods which call `handle_transition()` (so a plugin transition handler can intervene) and delegate the work to `fleet/services/` (kept for testability): `Deployment.deploy() / recover() / schedule(trip)`, `MaintenanceTask.start_task() / complete_task() / cancel_task()`, `FieldTrip.prepare_kit() / start_trip() / reconcile_trip() / cancel_trip()`, `Alert.acknowledge() / resolve()`. The services use the `can_*` properties as guards; the status lists moved into the `*StatusGroups` classes (`DEPLOYABLE`, `STARTABLE`, `KIT`, `RECONCILABLE`, `CANCELLABLE`).
- **Events:** new `fleet/events.py` (`FleetEvents`, e.g. `deployment.deployed`, `maintenancetask.completed`, `fieldtrip.closed`, `alert.opened`, `deployment.device_state_changed`). `trigger_status_event()` fires the event of the status just saved, from every service which saves a status (bulk updates fire one per object). As in core, they only reach anything when plugin events are enabled.
- **Status classes instead of TextChoices** for the cached condition fields: `HealthStatus` (UNKNOWN 10, OK 20, DEGRADED 30, CRITICAL 40), `AlertSeverity` (INFO 10, WARNING 20, CRITICAL 30) and `DataStreamStatus` (UNKNOWN 10, OK 20, LATE 30, MISSING 40); `Deployment.health`, `Alert.severity` and `DataStream.state` are integers (the migration converts the stored text). Severities are ordered, so escalation is `new > old`. They are served by `/api/generic/status/` and `/api/fleet/status/{health,severity,stream}/`, and rendered with `StatusRenderer` (type = class name). Device states stay TextChoices (annotated strings).
- **`FieldTrip.responsible` is an `Owner`** (a user or a group), as `Build.responsible`; the migration maps each user to its owner. "My trips"/"assigned to me" use `Owner.get_owners_matching_user()`.
- **Barcodes:** `Deployment` (`DP`), `MaintenanceTask` (`MT`) and `FieldTrip` (`FT`) have `InvenTreeBarcodeMixin`.
- **Validation in `Model.clean()`:** a deployment cannot replace itself and only a replacement replaces another one; a task needs a single serialized unit matching its deployment (`MaintenanceTask.validate_device`, also used by the service); a trip cannot end before it starts. The duplicate serializer checks were removed.
- `DeviceLink.notes` renamed to `comment` (`notes` is the markdown field in InvenTree).
- `PluginValidationMixin` was already there (it is part of `InvenTreeModel`).

**Services:** the manual `status_custom_key = None` resets were removed where `save()` runs (`StatusCodeMixin.save()` already clears a custom key that no longer matches; kept in bulk `.update()` calls); the kit split uses `StockItem.splitStock()` directly (as `move()` does) instead of comparing the location contents; `helpers.get_int_setting` was removed (core `get_global_setting` already casts settings with an `int` validator) and `get_model_setting(key)` takes the model from the setting definition (`InvenTreeSetting.model_class()`); notifications are sent with `transaction.on_commit` (nothing is sent for a rolled-back change; tests patch `fleet.notify.on_commit` to send at once). `tag_tracking` stays: passing the task into the tracking rows would need changes to core `StockItem` methods. The `Build` post_save receiver first runs one query (`pipeline.is_fleet_build`) and returns for other build orders, without a savepoint: core `build.test_api.BuildTest.test_complete` has a 450-query cap and went to 458 with the fleet hook's two queries and savepoint per build save.

**API:**
- Action endpoints follow the core pattern: `FleetActionMixin` puts the object in the serializer context (`deployment`, `task`, `trip`, `alert`) and `serializer.save()` performs the action (calling the model transition methods); the response is still the updated object (200). `raise_drf_error` and the `try/except` blocks are gone (the core exception handler converts Django validation errors).
- Token scopes of an action include its extra roles (e.g. deploy needs `r:change:fleet` **and** `r:change:stock`), built from `rolemap` and `extra_roles`.
- Users are `*_detail` objects (`UserSerializer`): `acknowledged_by_detail`, `resolved_by_detail`, `started_by_detail`, `completed_by_detail`, `technicians_detail`, `created_by_detail`, `team_detail`, `state_changed_by_detail`, `internal_state_changed_by_detail`; `responsible_detail` is an `OwnerSerializer`. The `*_name` fields are gone. New `health_text`, `severity_text`, `state_text`, `barcode_hash`; build brief has `status_text`; calendar events have `status_text`.
- `InvenTreeDecimalField` for quantities; `@register_importer()` on device types, sites and fault codes; `meta_path(DeviceLink)`; `AlertFilter.device` is a `ModelChoiceFilter`, `opened_after/before` use `InvenTreeDateFilter` (before = strictly before, as core `created_before`); `notes` removed from the trip search fields; the trip "Removed" location is an annotation (no query per row); the calendar filters tasks and trips in the database; the overview uses one aggregate per model.
- **Global search** (`InvenTree/api.py`, core file): `deployment`, `maintenancetask`, `fieldtrip`, `site`.

**Frontend:** status codes from `useStatusCodes` instead of hard-coded numbers; health, severity and stream state rendered from the backend status classes; user/owner detail columns (`UserColumn`, `ResponsibleColumn`); barcode actions and printing on the deployment, task and trip pages; reports enabled on the fleet tables; `showApiErrorMessage`; custom status rows; `getDetailUrl` breadcrumbs; core `OverdueFilter`/`AssignedToMeFilter`; `formatDate`; build order links; a table-style *Prepare kit* form (several lines at once); team errors shown under the team control; `PluginPanelKey.fleet`; global search entries; shared bootstrap (`src/functions/bootstrap.tsx`, used by both entries; the portal now loads the window globals); shared detail field lists (`src/fleet/details/FleetDetailFields.tsx`); main-UI Playwright tests (`tests/pages/pui_fleet_main.spec.ts`).

**Not changed, on purpose:** the API version (stays 511, section 8); the `.po` translation sources (the image build extracts them, section 8); the stock history codes 130–132 (the fork's own codes 58, 59 and 120–122 are in the same range, so this is the fork's convention); Teams/email stay fleet channels in `notify.py` (a built-in Teams notification method would be a plugin class and would lose the alert cards); tags on fleet models; squashing the migrations.

---

## 3. Frontend: shared building blocks (`F/src/fleet/`)

Register these model types in `F/lib/enums/ModelType.tsx`, with a `ModelInformation` entry each in `F/lib/enums/ModelInformation.tsx` (the entry is mandatory) and a renderer in `F/src/components/render/Instance.tsx` (in a new `F/src/components/render/Fleet.tsx`):
- `fleetsite`
- `fleetdeployment`
- `fleettask`
- `fleettrip`
- `fleetalert`
- `fleetdevicetype`

> **Changed in P1: use the backend model names as the ModelType values.** Attachments, notes (`model_type` is validated against the lowercase Django model name), per-model permissions (`user.permissions[model]`) and report templates all key on the Django model name. `ModelInformationDict` is also looked up by the enum *value*. So the values are:
> - `site`, `deployment`, `fleetdevicetype` (added in P1);
> - `maintenancetask`, `fieldtrip`, `alert` (P2–P4), not `fleetsite`, `fleettask`, … as listed above.
>
> `backendMappings.statusCodeList` maps `DeploymentStatus` → `ModelType.deployment`. Add `TaskStatus`, `TripStatus` and `AlertStatus` together with their model types.
>
> Icons added in P1: `fleet` (IconBuildingLighthouse), `fleet_site` (IconMapPin), `fleet_deployment` (IconAnchor), `fleet_device_type` (IconRadar).

Each entry sets `url_overview`/`url_detail` under `/fleet/...`, `api_endpoint`, and an icon. Add new icons to `F/src/functions/icons.tsx` (tabler icons: `IconBuildingLighthouse`, `IconRoute`, `IconAlertTriangle`, `IconTool`, `IconSailboat`). Add status classes to `F/src/defaults/backendMappings.tsx` `statusCodeList`. Add every endpoint to `F/lib/enums/ApiEndpoints.tsx` (`fleet_site_list = 'fleet/site/'`, …).

**Done in P1** (file layout to follow):
- `F/src/fleet/components/`: `FleetBadges.tsx` (`CoverageBadge`, `HealthBadge`, `RiskBadges`, `severityColor`) and `PipelineKpis.tsx` (tiles from `overview/`).
- `F/src/fleet/tables/`: `DeploymentTable` (modes pipeline / active / all; "Plan Deployment" button), `SiteTable`, `DeviceTypeTable`, `StreamTemplateTable`.
- `F/src/fleet/panels/`: `BuildDeploymentPanel` (+ `useBuildDeployment`) and `PartFleetPanel`.
- `F/src/forms/FleetForms.tsx`: site, device type, stream template, deployment (plan/edit), deploy, recover and assign-device fields.
- `F/src/components/render/Fleet.tsx`: `RenderFleetSite`, `RenderDeployment`, `RenderFleetDeviceType`.

**Done in P2:**
- Model type `alert` (`ModelInformation`: overview `/fleet/index/alerts`, no detail page, so in-app notifications link to the Alerts panel), renderer `RenderFleetAlert`, `AlertStatus` → `ModelType.alert` in `statusCodeList`, icon `fleet_alert`, and the endpoints `fleet_deployment_map|track|verify`, `fleet_stream_list`, `fleet_alert_list|acknowledge|resolve`.
- `F/src/fleet/components/`: `FleetMap.tsx` (`FleetMap` with props `deployments`, `onSelect`, `track`, `height`, `showGeofence`; `FleetMapView` loads `deployment/map/` and, for one deployment, its track), `OverviewKpis.tsx`, `StreamStatusList.tsx` (+ `formatAge`); `FleetBadges.tsx` gained `SeverityBadge`, `StreamStateBadge`, `alertTypeLabel`, `healthColor` and filter choices; `PipelineKpis.tsx` exports `KpiTile` and `useFleetOverview`.
- `F/src/fleet/tables/`: `AlertTable` (row actions acknowledge and resolve; *create task* comes with P3; rows open the deployment), `DataStreamTable`.
- `F/src/fleet/panels/`: `DeploymentHealthPanel` (live status, streams, "Check now" = verify, stream settings) and `StockFleetPanel` (+ `useStockFleetInfo`).
- `FleetForms.tsx`: `dataStreamFields`, `resolveAlertFields`, `deviceLinkFields`.
- Map: `leaflet` 1.9.4, `react-leaflet` 5.0.0 (supports React 19), `@types/leaflet` 1.9.22. Pins are `CircleMarker`s (no marker image assets), tiles from OpenStreetMap with attribution.

**Done in P3:**
- Model type `maintenancetask` (`ModelInformation`: overview `/fleet/index/maintenance`, detail `/fleet/task/:pk/`), renderer `RenderFleetTask` (reference, site nickname or serial, status), `TaskStatus` -> `ModelType.maintenancetask` in `statusCodeList`, icon `fleet_task` (IconTool), endpoints `fleet_task_*`, `fleet_checklist_item_list`, `fleet_kit_line_list`, `fleet_fault_code_list`, `fleet_task_checklist_list`, `fleet_task_action_list`, `fleet_alert_create_task`.
- **`useComponentServiceForms`** in `F/src/forms/ComponentServiceForms.tsx` (not under `F/src/fleet/`: it is the generic stock component service). Props: `unit`, `url` (default `stock/:id/components/`), `pk` (default the unit), `extraFields` (all four forms), `removalFields` (remove and destroy only), `defaultLocation` (default: unit location, else part default location; `null` = blank), `location` (field overrides), `table`, `onSuccess`. Returns `openInstall()`, `openRemove(record)`, `openDestroy(record)`, `openReplace(record)` and `modals`. The four forms are the old InstalledItemsTable modals, field for field. `InstalledItemsTable` now only calls the hook (same fields, initial data, titles, messages and row actions).
- `F/src/fleet/components/`: `TaskExecution.tsx` (checklist, live streams when the device is in the water, actions *Consume*, *Repair / Note* (record-action), *Reposition*, a hint to add photos as attachments, *Verify data* with the stored result, *Complete* (override reason shown only when the check has not passed), the installed components with Replace / Remove / Mark destroyed / Add through the hook and `fleet/task/<pk>/component-action/`, extra fields fault code and checklist item, "Stream no longer reported" on remove/destroy, blank removal location = trip "Removed" or workshop, and the recorded actions), `ChecklistEditor.tsx` (OK / Issue / N/A per item, value for measurements, details form with fault code; *Issue* saves first and then opens the details; an issue offers *Record an action*), `TaskCalendar.tsx` (FullCalendar via the core `Calendar` + `useCalendar`; tasks on their scheduled date, else due date; dragging sets `scheduled_date`). `PipelineKpis.tsx` gained `MaintenanceKpis`; `FleetBadges.tsx` gained `taskTypeLabel`, `taskTypeChoices`, `maintenanceActionLabel`, `checklistResultInfo`; `DeploymentHealthPanel.tsx` exports `VerifyResult`.
- `F/src/fleet/tables/`: `TaskTable` (Site / Device column = site nickname, else serial; overdue due dates in red; filters open, status, type, overdue, assigned to me, has trip; *New Task* for fleet add, with the device / deployment pre-set when given), `ChecklistTemplateTable`, `KitTemplateTable`, `MaintenanceActionTable`. `AlertTable` gained the row action *Create Task* (follows to the new task).
- `FleetForms.tsx`: `checklistItemFields`, `kitLineFields`, `useTaskFields`, `checklistResultFields`, `taskComponentFields`, `taskRemovalFields`, `consumeFields`, `repositionFields`, `recordActionFields`, `completeTaskFields`, `cancelTaskFields`, `alertCreateTaskFields`, and related-field renderers `renderFaultCode`, `renderChecklistResult`, `renderDataStream` (these models have no frontend `ModelType`, so the forms pass a `modelRenderer`).
- Scan-to-pick: the stock item fields of the component forms are normal related fields, so the barcode button appears when the global setting `BARCODE_ENABLE` and the **user** setting `BARCODE_IN_FORM_FIELDS` ("Barcode in form fields") are on. Technicians must switch on that user setting; put it in the P6 user guide.

**Done in P5** (shared by the main UI and the portal; all LF):
- **Action hooks** in `F/src/fleet/hooks/`. Each returns `state` (what the object is and what the user may do), `modals` (render once) and `open*` functions. The main UI pages now use them too, so both UIs share the same dialogs and rules:
  - `DeploymentActions.tsx`: `useDeploymentState`, `useDeploymentActions` (edit, **plan** = site and target date only, delete, create build order, assign device, deploy, set position, recover) and `DeploymentStatus`.
  - `TaskActions.tsx`: `useTaskState`, `useTaskActions` (start, edit, cancel, delete; `onStarted`) and `TaskStatus`.
  - `TripActions.tsx`: `useTripState`, `useTripActions` (start, edit with the team, cancel, delete, **Add Tasks** picker) and `TripStatus`. `AddTasksModal` moved here from `pages/fleet/TripDetail.tsx`.
- `F/src/fleet/components/ComponentTree.tsx`: read-only tree of the installed components (`GET stock/<pk>/components/`, one level at a time).
- `F/src/fleet/components/DeploymentTimeline.tsx`: history of a deployment, newest first (planned, deployed, completed tasks, the last 25 alerts, recovered).
- `DeploymentTable`: columns **Last Data** (age of `last_contact`) and **Days Deployed**; filters **Client**, **Health**, **PM Due Before**, **PM Overdue** (not in pipeline mode); in pipeline mode the row actions **Set Site and Date**, **Create Build Order** and **Assign Device**, and the query asks for `build_detail` (an open build order hides *Assign Device*).
- `FleetForms.tsx`: `planDeploymentFields()` (site, target date).

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

**Done in P1:**
- The Fleet nav tab, drawer entry and spotlight action, and the routes `/fleet/index/:panel`, `/fleet/site/:id/*`, `/fleet/deployment/:id/*` and `/fleet/device-type/:id/*`.
- `FleetIndex` panels: Pipeline (KPI tiles + pipeline table with risks), Deployments (active), Sites, Device types. Overview, Alerts, Maintenance, Trips and Calendar are added in later phases.
- `SiteDetail`: details, deployments, attachments, notes.
- `DeploymentDetail`:
  - Details, Readiness (risks; pipeline only), attachments and notes.
  - Actions: Create Build Order, Assign Device, Deploy, Recover, edit and delete.
  - The `metadata_warning` (BO quantity ≠ 1) shows as an alert, and a blank platform id shows as "Not configured".
- `DeviceTypeDetail`: details, data streams (stream templates), deployments, notes. The checklist and kit templates come in P3.
- `BuildDetail`: a "Deployment Plan" panel, shown only when a deployment is linked.
- `PartDetail`: a "Fleet Device" panel on trackable assemblies, to create or edit the FleetDeviceType.
- System Settings has a **Fleet** panel listing every `FLEET_*` setting (section 2.3 asked for it; P0 had not added it).

**Done in P2:**
- `FleetIndex` gained **Overview** (first panel, so `/fleet/` opens it: KPI tiles, map of deployed devices, open alerts) and **Alerts** (all alerts). The spotlight action now opens the overview.
- `DeploymentDetail`: panels **Health and Streams** (deployed only), **Map and Track** (deployed only) and **Alerts** (not for pipeline deployments).
- `SiteDetail`: panels **Map** (when the site has an active deployment) and **Alerts**.
- `StockDetail`: a **Fleet** panel for serialized items whose part (or its template) has a device type, or which already have a platform link or deployments: current deployment card with live streams, data platform link (create or edit the platform id and firmware), deployment history. The maintenance history comes with P3.
- Dashboard widget **Critical Fleet Alerts** (`QueryCountDashboardWidget`, open CRITICAL alerts). The PM-overdue, unscheduled-pipeline and my-tasks widgets come with P3.

**Done in P2b:**
- `DeploymentDetail`:
  - a **Set Position** action (fleet change, any open deployment; orange while the position is empty) with latitude, longitude, depth and geofence radius, pre-filled from the deployment or the site;
  - a "Position: Not set" row on deployed devices;
  - the deploy dialog says the site and the position are optional.
- `DeploymentTable`: a **Client** column (hidden by default in pipeline mode; the table requests `client_detail`), a **Position** column (coordinates or an orange "Not set" badge; not in pipeline mode), and the filters **Has Site** and **Has Position**.
- `OverviewKpis`: tiles **No position** and **No site**.
- `FleetMapView`: below the map, "Devices without a position (not shown on the map): N", or for a single deployment a hint to use *Set Position*.
- `FleetForms.tsx`: `setPositionFields()`. `ApiEndpoints`: `fleet_deployment_set_position`.

**Done in P3:**
- Route `/fleet/task/:id/*` -> `TaskDetail`: details (type, description, device, deployment, site, trip, follow-up of, dates, technicians, labour, as found, summary; an orange note when closed without a passed data check), **Execution** (`TaskExecution`, while IN_PROGRESS), **Checklist** (read-only, with the stored data check) and **Actions** after the task (closed), **Alerts** (when linked), attachments (photos), notes. Actions: *Start* (proposed/scheduled), print menu with reports (the service report), admin, edit / cancel (open tasks), delete (proposed or cancelled, no actions).
- `FleetIndex`: **Maintenance** panel (after Deployments) with a table / calendar switch (`SegmentedControlPanel`, choice kept in local storage): `MaintenanceKpis` + `TaskTable`, or `TaskCalendar`. A separate *Calendar* panel (deployments, tasks and trips) is still open; it fits P4/P5 with trips.
- `DeviceTypeDetail`: panels **Checklist** (`ChecklistTemplateTable`) and **Parts Kit** (`KitTemplateTable`).
- `DeploymentDetail`: **Maintenance** panel (not for pipeline deployments): `TaskTable` for the deployment, *New Task* pre-filled with the device and deployment.
- `SiteDetail`: **Maintenance** panel (tasks of the site).
- `StockDetail` Fleet panel (`StockFleetPanel`): **Maintenance History** table.
- Dashboard widgets: **Fleet PM Overdue** (`deployment?pm_overdue=true`), **Unscheduled Fleet Deployments** (`deployment?unscheduled=true`), **My Fleet Tasks** (`task?assigned_to_me=true&open=true`).

**Done in P4:**
- **Registration.**
  - Model type `fieldtrip`, with `ModelInformation`: overview `/fleet/index/trips`, detail `/fleet/trip/:pk/`, icon `fleet_trip` (IconSailboat).
  - Renderer `RenderFleetTrip`; `TripStatus` -> `ModelType.fieldtrip` in `statusCodeList`.
  - Endpoints `fleet_trip_*` and `fleet_task_deploy`.
- **Route `/fleet/trip/:id/*` → `TripDetail`.**
  - Header with status steps (Planning → Kit ready → In progress → Reconciling → Closed; a cancelled trip shows an alert instead).
  - Actions: Start, Edit, Cancel, Delete, print menu (trip report).
  - **Details** panel.
  - **Tasks** panel: `TaskTable` for the trip. *Add Tasks* opens a picker with two tabs: open tasks without a trip, and READY deployments. The row action *Remove from Trip* is there for tasks that have not started.
  - **Kit** panel: `TripKitTable` with available / in kit / missing, *Suggest Kit*, *Add Part* (MANUAL), *Take Stock*, and a per-line *Take* (prepare kit, one stock item at a time). Below it, the *Kit Contents* and *Removed Components* tables.
  - **Reconcile** panel (`TripReconcilePanel`): leftovers and removed items with their default destination, an optional location per item, *Reconcile*, and the result (closed, or why not).
  - Attachments and notes.
- **FleetIndex.**
  - A **Trips** panel (`TripTable`: filters open, status, mine; *New Field Trip*).
  - The separate **Calendar** panel (`FleetCalendar`: deployments, tasks and trips from `calendar/`, coloured by status), which was open since P3.
- **Plan selection (tick tasks → trip).** `useTripSelectionActions` in `F/src/fleet/components/TripSelection.tsx` adds *Create Field Trip* (fleet add; sends the selected `tasks` / `deployments`) and *Add to Trip* (pick an open trip).
  - It is used by the Maintenance panel's `TaskTable` (`selection='trip'`): only PROPOSED/SCHEDULED tasks **without a trip** can be ticked.
  - It is also used by the Pipeline panel's `DeploymentTable`: only READY deployments can be ticked.
- **Team picker.** The trip `team` many-to-many field has no form field type, so `TeamSelect` is a separate multi-select under *Responsible*, sent with the create and edit requests.
- **`TaskExecution`.**
  - A *Deploy Device* card for DEPLOYMENT/SWAP tasks whose deployment is not deployed yet (needs stock change). For a swap, a note says where the old device goes.
  - *Consume* is limited to the trip kit location.
- `TaskDetail` links the trip. `alertCreateTaskFields` has `trip`.

**Done in P5:** `DeploymentDetail`, `TaskDetail` and `TripDetail` use the shared action hooks (section 3, "Done in P5"); the buttons, menus and rules are unchanged. The Pipeline panel's table has the new row actions (*Set Site and Date*, *Create Build Order*, *Assign Device*) and the Deployments table the new columns and filters.

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

**As built in P5 (build and serve):**
- **Vite** (`F/vite.config.ts`, CRLF kept): `build.rollupOptions.input = { index, fleet }` (absolute paths via `fileURLToPath`), same `outDir`. A small plugin `fleetPortal()`:
  - `vite dev`: rewrites `/fleet`, `/fleet/…` to `fleet.html` (otherwise the dev server answers with the main `index.html`);
  - `vite build`: **fails the build if either entry is missing from the bundle** (see the gotcha below).
  - `F/fleet.html` is `index.html` with the title "InvenTree Fleet" and the script `/src/portal/main.tsx`.
  - The manifest has both keys `index.html` and `fleet.html`. The main entry keeps its key and works as before; with two entries Rollup moves the code shared by both (React, Mantine, the core CSS) into shared chunks, which the entry chunks import statically (the browser loads them by itself).
- **Django:**
  - `web/templatetags/spa_helper.py`: `spa_bundle(manifest_path='', app='web', entry='index.html')`; an entry missing from the manifest logs an error and returns `NOT_FOUND` (the page then shows INVE-E1). `spa_settings(base_url='')`: with a base url the settings JSON gets `base_url` overridden and `main_base_url` = the main UI path (`FRONTEND_URL_BASE`), so the portal can link to the main UI.
  - `web/templates/web/fleet.html`: the fork's `index.html` template with `{% spa_bundle entry='fleet.html' %}`, `{% spa_settings base_url='fleet' %}` and the title "Fleet | <instance>". The deploy image replaces it with `D/templates/spa_fleet.html` (same change on top of the branded `spa_index.html`).
  - `web/urls.py`: `path('fleet/', include([re_path('.*', fleet_view, name='fleet-portal')]))` and `fleet` → redirect to `/fleet/` (`fleet-portal-root`, auth exempt). `fleet_view = ensure_csrf_cookie(TemplateView.as_view(template_name='web/fleet.html'))`.
  - **`InvenTree/middleware.py` (not in the original list):** `/fleet/` is added to `paths_own_security` and `fleet-portal`, `fleet-portal-root` to `pages_mfa_bypass`, exactly like the main frontend. Without it an anonymous visit to `/fleet/` is redirected to the allauth login, which redirects to `/web/`, and a user who must still set up MFA gets a JSON 401 instead of the portal page (which has the MFA setup screen).
  - `InvenTreeHostSettingsMiddleware` (INVE-E7): with the default lax check (`INVENTREE_SITE_LAX_PROTOCOL`, on by default) another port of the same host name passes. With the strict check the portal origin (`https://host:8444`) must be in `INVENTREE_TRUSTED_ORIGINS` (it must be there anyway for CSRF, see section 9). `fleet/test_portal.py` covers both.
- **Portal app (`F/src/portal/`, all LF):**
  - `main.tsx`: the CSS imports of `src/main.tsx`, the same settings merge, then `base_url` forced to `fleet` (also under `vite dev`), Sentry. `PortalView` is **lazy-loaded**, because shared modules (e.g. `defaultHostList`) read `window.INVENTREE_SETTINGS` when they load.
  - `PortalView.tsx`: `setApiDefaults()` when the module loads (before the first page query), default host list, `ApiProvider`, `ThemeContext`, `BrowserRouter basename='fleet'`. No `MainView`, so no mobile blocker. It imports `portal.css` (see the CSS gotcha).
  - `PortalRoutes.tsx`: the screens below, the login pages (`pages/Auth/*` imported directly, not through `router.tsx`) inside `LoginLayoutComponent`, and redirects for links shaped like the main UI (`navigation.tsx`): `/fleet/…` (a main UI fleet link inside the portal) drops the prefix; `/index/<panel>` → the matching screen (`alerts`, `maintenance`/`calendar` → `/plan`, `trips`, `sites`, `pipeline`, `deployments`/`overview` → `/`); `/trip/<id>` → `/trips/<id>`; `/site/<id>` → `/sites/<id>`; `/home` → `/`; anything else (stock items, parts, build orders, device types) opens the main UI (`/web/…`, using `main_base_url`). This lets every shared table, form (`follow`) and link work unchanged in the portal.
  - `PortalLayout.tsx`: `ProtectedRoute` (from `components/nav/Layout`, so `checkLoginState` on reload), fleet view role check (`PermissionDenied`), Mantine `AppShell`. **Desktop/tablet (≥ 768 px):** header (Fleet title, user menu: "Open InvenTree", "Log out") and a side navigation. **Phone (< 768 px, `useIsPhone`):** no side navigation at all, a bottom tab bar (Overview, My Work, Trips, Alerts, More → Pipeline, Plan, Sites, Open InvenTree, Log out).
  - `portal.css`: tab bar; on phones touch targets of at least 44 px (buttons, menu items, tabs, segmented controls), notifications above the tab bar, the login box without its fixed 425 px minimum width.
  - Components: `PortalPage` (back button, title, badges, actions that wrap), `PortalTabs` (panel objects like `PanelGroup`, selected tab kept in `?tab=`, scrolls sideways, `Suspense` for the lazy notes editor), `PortalCards` (`TaskCard`, `TripCard`, `LinkCard`, `CardList`: whole card is the touch target), `PortalDetails` (`Detail` label/value), `useIsPhone`.

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

**As built in P5 (screens, `F/src/portal/pages/`):** nothing needs a site or a position; devices without a site show their serial.
- `/` **Overview**: `OverviewKpis`, `FleetMapView` (shorter on phones), open alerts (`AlertTable openOnly`: acknowledge, resolve, create task), deployed devices (`DeploymentTable mode='active'` with the new Last Data / Days Deployed columns and Client / Health / PM Due Before / PM Overdue / Coverage filters).
- `/pipeline`: `PipelineKpis` and three tabs (remembered): **Unscheduled** (`unscheduled=true`; row action *Set Site and Date*), **Scheduled** (target date set, soonest first, risk badges), **Ready** (status READY, tick → *Create Field Trip* / *Add to Trip*). *Plan Deployment*, *Create Build Order* and *Assign Device* are in the table.
- `/plan`: **Tasks** view = `MaintenanceKpis`, map, open tasks without a trip ordered by due date (`TaskTable selection='trip'`: tick → *Create Field Trip* / *Add to Trip*), with a note on the PM horizon (`FLEET_PLAN_HORIZON_DAYS`; PM tasks are only proposed inside it, so no extra date filter); **Calendar** view = `FleetCalendar`. The view is remembered.
- `/trips` (`TripTable`) and `/trips/:id`: header with status and `TripStatusSteps`; actions *Start Trip*, *Add Tasks*, print (trip report), More (edit, cancel, delete) through `useTripActions`; tabs **Tasks** (cards grouped by site nickname, or the `TaskTable` with *Remove from Trip*), **Kit** (`TripKitTable`, kit contents, removed components), **Reconcile** (`TripReconcilePanel`), Details, Attachments, Notes.
- `/my` (technician): cards for **Trips Today** (in progress, or started on/before today), **My Tasks** (`assigned_to_me`, open; in progress first, then by due date) and **Upcoming Trips**.
- `/deployment/:id` (device card): site (link), device (link to the main UI stock item), platform id, client, coverage, last data, target/deployed date, next PM, position ("Not set"), build order. Actions *Start Maintenance* (a dialog with the open tasks of the device as cards, and *New Task* for managers), *Deploy*, *Recover*, *Set Position*, *Create Build Order*, *Assign Device*, More (*Set Site and Date*, edit, delete). Tabs **Live Data** (streams + map snippet, deployed only), **Readiness** (pipeline), **Components** (`ComponentTree`, read only), **Alerts**, **History** (`DeploymentTimeline`), **Maintenance**, Attachments, Notes.
- `/task/:id`: header (status, type, overdue), *Start Task* (then the Work tab opens), print (service report), More (edit, cancel, delete). Tabs **Work** (`TaskExecution`, while in progress), Details, **Checklist** and **Actions** (closed tasks), Alerts, Attachments (photos), Notes.
- `/alerts` (`AlertTable`), `/sites` (`SiteTable`) and `/sites/:id`: details, three tiles (**Deployed time, % of the last 12 months** = union of the deployed periods of the site's deployments; this is "uptime" as time with a device in the water, not data availability; **Visits** = completed tasks in 12 months; **Devices** deployed there), tabs **Devices** (one card per deployment with its dates: devices over time), **Visits** (`TaskTable`), Alerts, Map (when a device is deployed), Attachments, Notes.

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
      - *P1 tests the BUILD_LATE risk only.* The notification needs `notify.py` (P2) and `pipeline_alerts()` (planning). Extend `test_scenario_1_pipeline_from_bo_issue` in P2 or P3, whichever adds `pipeline_alerts`. *P2 added `notify.py` but not `pipeline_alerts()` (it belongs to `planning.py`), so this is left for P3.*
      - **Done in P3:** the scenario runs `pipeline_alerts()` twice with a mocked webhook: one BUILD_LATE alert (OPEN) and exactly one Teams card ("Build late - Algarve #1").

   Scenarios 1–3 are covered by `fleet/test_pipeline.py` (services, using the real build flow) and `fleet/test_api.py`. Scenario 3 is the "deploy without a trip" variant; the trip part (3.1) comes with P4.
   Scenarios 5 (without a trip: steps 5.1, 5.4–5.7; 5.2 is scheduling by date, 5.3 and 5.8 need trips), 6, 8 and 9 are covered by `fleet/test_maintenance.py` (P3), with service and API tests.
   **P4:** `fleet/test_trips.py` covers scenario 5 **in full** (`test_scenario_5_full`: trip from the selected task, kit location and Removed child, kit suggestion with availability, prepare kit + "ready" card, replace from the kit into Removed, consume from the kit only, verify, complete, reconcile to default location and workshop, CLOSED, trip report context), scenario **7** (`test_scenario_7_swap`) and **3.1** (`test_deploy_on_trip`), plus the trips rules, kit suggestion, failure history, report rendering, the trip API, OPTIONS, schema and permissions.
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
   - **Done in P5:** `F/tests/pages/pui_fleet.spec.ts`. *Scenario 10* creates its own data through the API (trackable part, one serial unit, customer, device type with an essential stream and a required checklist item, deployment assigned and deployed, platform id, PREVENTIVE task; the data platform is the built-in mock), logs in at `/fleet/login`, checks the overview (KPI tiles, map), opens the deployment from the deployed devices table, checks the device card (serial, streams, Components, History), uses *Start Maintenance* → task → *Start Task* → checklist OK → *Verify data* (passed) → *Complete* with a summary → Completed, and recovers the device at the end. *Phone width* (390×844, touch): login page and screens without sideways scrolling, bottom tab bar instead of the side navigation, tab bar buttons ≥ 44 px, "More" menu, no mobile blocker. *Links from the main UI shape*: `/fleet/index/alerts`, `/fleet/fleet/index/maintenance` and a main UI path redirect correctly.

---

## 7. Implementation phases (one PR-sized milestone each; stop for review after each)

**Definition of done for every phase** (product owner, 2026-10-08), in addition to the phase's own criteria:
1. The **whole** backend suite passes on Linux (`make test`, see section 8), including tests that were already failing before the phase. Fix them; don't list them as known failures.
2. The deploy Dockerfile overlay is updated **in the same phase** for every new or changed backend file (section 9).
3. The deploy image builds with the phase's code, and Django starts inside it (`check`, `migrate` on an empty database, `makemigrations --check`).


| Phase | Scope | Done when |
|---|---|---|
| **P0 Foundations** | App skeleton, registration, role `fleet` (ruleset, scopes, frontend `Roles.tsx`; no users migration needed), settings, status codes, **all models** plus `0001`/`0002` migrations, admin, StockHistoryCode 130–132 | `fleet`, `users.tests`, `common` settings tests and `makemigrations --check` pass; plus the definition of done above (whole suite green, Dockerfile overlay updated, image builds and starts) |
| **P1 Sites, device types, pipeline** ✅ done 2026-10-08 | Services `pipeline` and `deployment` (minus monitoring); APIs for device-type, site, deployment (+ actions), calendar, overview (pipeline part); signals plus the sync task; frontend model types, renderers, Fleet tab, FleetIndex (Pipeline, Deployments, Sites, Device types), Site/Deployment/DeviceType detail, Build and Part panels; `fleet_import_existing` command | Scenarios 1, 2 and 3 (deploy without a trip) pass. **Result:** 1368 backend tests OK on Linux (`make test`, 0 failures); image built from a scratch copy; `check`, `migrate` on empty Postgres 17, `makemigrations --check` and the 0003 reverse/forward all pass; frontend extract/compile/tsc/build pass in the image; tsc and Biome are clean on the changed files |
| **P2 Monitoring and alerts** ✅ code done 2026-10-08 (Teams manual check pending) | Integration client (mock + http skeleton), `monitoring` service, poll task, streams, positions, geofence (`geo.py` with unit tests), alerts plus the alert API, `notify.py` (Teams, email, in-app), FleetMap, Overview panel, Alerts panel, StockDetail Fleet panel | Scenario 4 passes; a Teams card was checked manually against a test channel. **Result:** scenario 4 and the monitoring part of scenario 8 pass; 94 fleet tests and 1421 backend tests OK on Linux (`make test`, 0 failures); image built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check` and the fleet reverse/forward migration pass; local tsc and Biome clean on the changed files. **Still open:** the manual Teams card check (needs a test channel webhook) |
| **P2b Stock-driven deployments** (added 2026-10-08) | Section 2.10: `stock_sync` service + scheduled call + `fleet_sync_stock --dry-run` command; remove `fleet_import_existing`; site optional in deploy; per-device position (Set position action, Position column, filters, KPIs); sales visibility per the answer to question 8 (option b) | Section 2.10 tests pass; plus the definition of done. **Result:** 1440 backend tests OK on Linux (`make test`, 0 failures, 726 s), including 113 fleet tests. Two earlier runs on a busy machine (other containers running) each failed one different non-fleet test on the 7.5 s per-request time limit only; on a quiet machine the whole suite passes. Image built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check`, fleet migration reverse/forward and `fleet_sync_stock --dry-run` pass; Ruff, tsc and Biome clean on the changed files |
| **P3 Maintenance** ✅ done 2026-10-08 (questions 6 and 9 still open) | Checklist and kit templates, `planning` (PM proposals and alerts), `maintenance` service and task API, `useComponentServiceForms` refactor, TaskExecution, Task detail, service report | Scenarios 5 (without trip), 6, 8 and 9 pass; InstalledItemsTable behaviour unchanged (existing `stock/test_components.py` still passes). **Result:** **1467 backend tests OK on Linux** (`local-inventree.ps1 test`, 0 failures, 674 s, quiet machine), including 140 fleet tests (27 new in `test_maintenance.py`, scenario 1 extended) and `stock/test_components.py`. Image built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check`, fleet `0004` reverse/forward, `fleet_install_reports` and `fleet_sync_stock --dry-run` pass. Ruff, tsc and Biome clean on the changed files. Built with empty checklist/kit templates and the 180-day default (question 6) and without internal device states (question 9) |
| **P4 Field trips** ✅ done 2026-10-09 (questions 6 and 9 still open) | `trips` service and API, kit suggestion, prepare and reconcile, trip detail, plan selection (tick tasks → trip), trip report, swap | Scenarios 5 (full) and 7 pass. **Result:** **1486 backend tests OK on Linux** (`local-inventree.ps1 test`, 0 failures, 574 s), including 159 fleet tests (19 new in `test_trips.py`: scenarios 5 full, 7 and 3.1). Image built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check`, fleet `0005` reverse/forward, `fleet_install_reports` (service and trip report) and `fleet_sync_stock --dry-run` pass. Ruff, tsc and Biome clean on the changed files; the Dockerfile needed no change (no backend file outside `fleet/`). Not clicked through in a browser yet |
| **P5 Fleet Portal** ✅ done 2026-10-09 (questions 6 and 9 still open) | Vite second entry, Django template, URL and spa_helper changes, portal shell and screens (section 5.2), responsive layout | Scenario 10 passes; manually checked on a phone width. **Result:** **1495 backend tests OK on Linux** (`local-inventree.ps1 test`, 0 failures, 781 s), including the 9 new tests of `fleet/test_portal.py` (entries, settings, `/fleet/` paths, anonymous access, the :8444 host checks). Playwright `pui_fleet.spec.ts`: *Scenario 10*, *Phone width* and *Links from the main UI shape* pass (Chromium, local Django on a scratch SQLite database serving the built bundle, no retries). Checked by hand from screenshots at 390×844 (login, overview, My Work, device card, task flow, trip, plan, More menu: no sideways scrolling) and at 1366×900; the refactored main UI deployment, task and trip pages and the new pipeline row action were opened in a browser. Image built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check`, fleet `0005` reverse/forward, `fleet_install_reports` and `fleet_sync_stock --dry-run` pass; the image's `web/static/web` manifest has the entries `index.html` and `fleet.html`, and `/fleet/…` serves the branded portal page (`base_url` `fleet`, assets 200) while `/web/` keeps the main UI. tsc, Biome (TS/TSX) and Ruff (`--preview`) clean on the changed files |
| **P6 Deployment and docs** ✅ done 2026-10-09 (question 9 moved to a later phase, question 6 is data) | Caddy, compose and env changes (section 9; Dockerfile COPY lines are added by each phase), migration runbook, `D/docs/13-fleet.md` user guide, retire the plugin's Deployed Systems widget **only after sign-off** | Staging deploy verified end to end. **Result:** **1495 backend tests OK on Linux** (`local-inventree.ps1 test`, 0 failures, 636 s; P6 changed no backend file). `caddy validate` passes (caddy:alpine; the adapted config has a `:8444` server whose only extra route is `path /` → 302 `/fleet/`). Image built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check`, fleet `0005` reverse/forward, `fleet_install_reports`, `fleet_sync_stock --dry-run` pass; the image manifest has `index.html` and `fleet.html`. **Staging** = the full compose stack on the dev PC (scratch copy, project `inventree-p6-staging`, override for container names, image tag and a named DB volume), run with the new runbook order: worker stopped → one-off `invoke migrate` → `collect-static.sh` → `fleet_install_reports` → device type for a unit that already had a customer → dry run listed `Open: DP-0001 …` → worker started → deployment opened by the worker within 90 s. Through Caddy: `:8444/` → 302 `/fleet/`, `/fleet/` 200 with its assets from `/static/web`, `/web/` and `/api/` 200 on both ports. Playwright against `https://192.168.1.33:8444` (Chromium, temporary copy of the tests, no retries): *Scenario 10* (portal login, overview, device card, task Start → checklist → Verify data → Complete: session + CSRF POSTs from the `:8444` origin), *Phone width* (390×844), *Links from the main UI shape*, and a P6 spec (bare `:8444` → portal login; after the portal login the main UI on `:8443` is logged in; logout on `:8443` logs the portal out). Not checked on a physical phone (the staging stack was left running for that). The legacy widget stays (question 7: keep for now) |
| **P7 Internal device states** ✅ done 2026-10-09 (question 9 answered at the start: section 2.11) | Seven states (Decommissioned, Docked, Maintenance Scheduled, Maintenance Overdue, Problem Acknowledged, Unresponsive, Active) on the physical device, honoured by the stock sync, monitoring, planning, overview, map and lists; set-state action, badge and filter in both UIs | Product owner's confirmed scenarios pass; plus the definition of done. **Result:** **1513 backend tests OK on Linux** (`local-inventree.ps1 test`, 0 failures, 597 s, P6 staging stack stopped during the run), including 186 fleet tests (18 new: 4 in `test_stock_sync.py`, 4 in `test_monitoring.py`, 10 in `test_maintenance.py` for planning, maintenance and the API). Image built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check` ("No changes detected"), fleet `0006` → `0005` → `0006`, `fleet_install_reports` and `fleet_sync_stock --dry-run` pass; the manifest has `index.html` and `fleet.html`. Playwright `pui_fleet.spec.ts` (4 tests incl. the new *Device state*) passes against a throwaway server from the built image (Chromium, no retries); on the very first run, *Scenario 10* timed out at its first portal login right after the server started (still on the login form), and passed on the re-run and in a full second run. tsc, Biome and Ruff (`--preview`) clean on the changed files. The Dockerfile needed no change. The main UI deployment page was not clicked through in a browser (tsc only) |
| **P8 InvenTree conventions pass** ✅ done 2026-10-10 (requested by the product owner after a review of the module against the core apps) | Section 2.12: transitions on the models (`StateTransitionMixin`, `can_*`), `FleetEvents`, health/severity/stream state as status classes, `FieldTrip.responsible` → `Owner`, barcodes, `clean()` rules, `DeviceLink.comment`, core-style action endpoints and token scopes, user/owner detail fields, global search, importer, decimals, N+1 and DB-side filters, notifications on commit; frontend status codes from the backend, user/owner columns, barcode/print actions, shared detail fields and bootstrap, table-style kit form, main-UI Playwright tests | Definition of done. **Result:** **1528 backend tests OK on Linux** (`local-inventree.ps1 test`, 0 failures, 696 s), including 225 in `fleet`, `generic.states` and `users.tests` (new: `test_conventions.py` with 13 tests and `test_migrations.py` with the 0007 forward and reverse data checks). The first full run failed core `build.test_api.BuildTest.test_complete` (458 queries, cap 450) because of the fleet build hook; fixed with `pipeline.is_fleet_build` (section 2.12). **The P6 staging stack was not stopped during the runs** (stopping it was not allowed in this session); the suite still passed. Image `bo-inventree-p8-check` built from a scratch copy (frontend extract/compile/tsc/build inside); `check`, `migrate` on empty Postgres 17, `makemigrations --check` ("No changes detected"), fleet `0007` → `0006` → `0007`, `fleet_install_reports` and `fleet_sync_stock --dry-run` pass; the manifest has `index.html` and `fleet.html`. tsc clean, Biome clean on the changed frontend files, Ruff (`--preview`) clean on fleet. **Not run:** Playwright (`pui_fleet.spec.ts`, new `pui_fleet_main.spec.ts`); no click-through in a browser |

---

## 8. Testing and quality commands
- Backend tests: `make test` (all apps) or `make test TESTS="fleet users.tests"`, i.e. `dev/local-inventree.ps1 test [labels]`. Needs Docker Desktop running.
  - `make` isn't on the Git Bash PATH. From Git Bash or an agent, run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./dev/local-inventree.ps1 test [labels]`.
  - The full suite takes about 15 minutes (P0 baseline: 1341 tests; after P1: 1368).
- Deploy image check (each phase):
  1. Copy `D/Dockerfile`, `.dockerignore`, `plugins/`, `branding/` and `templates/`, plus the fork's `src/` (without `node_modules`), into a scratch folder.
  2. Build with `docker build -t bo-inventree-pN-check .`. The frontend stage runs `yarn run extract && yarn run compile && yarn run build`, including tsc.
  3. Run `manage.py check`, `migrate`, `makemigrations --check --dry-run` and `migrate fleet <prev>` / `migrate fleet` against a throwaway `postgres:17` container on a temporary network. In Git Bash, set `MSYS_NO_PATHCONV=1` for container paths.
- Yarn isn't installed globally on the dev machine. `yarn run extract` rewrites the **tracked** `src/locales/*/messages.po` files, so let the image build run extract/compile rather than running them in the checkout (or use `npx yarn@1.22.22` in a copy).
  - It runs `dev/ci-test.sh` in the `inventree/inventree:1.4.0` image (Linux), on a **copy** of the checkout, with the same preparation as the upstream CI job: git repo, `gettext`, fresh SQLite database, plugins enabled, `invoke migrate`, `invoke static`, `check_migration_files.py`, then `invoke dev.test --check --translations`.
  - Do **not** treat native Windows runs as the reference. WeasyPrint/label printing, `file://C:\…` static paths and the sample printer plugin fail on Windows (46 failures on 2026-10-08), and running against the dev database breaks tests that rely on content type ids.
  - In CI (Linux): `invoke dev.test --runtest=fleet` (and the other apps).
- Migrations:
  - `python src/backend/InvenTree/manage.py makemigrations --check --dry-run`
  - Reversibility: `migrate fleet zero`, then `migrate`.
- Schema: `invoke dev.schema`. Review the diff for `/api/fleet/`.
- Lint:
  - `ruff check src/backend/ && ruff format src/backend/`
  - `npx @biomejs/biome@1.9.4 check src/frontend`. On this Windows checkout (CRLF working copy), a full-tree check reports about 590 "format" errors in **untouched** files, which are line-ending differences only. Check the changed and new files instead: `npx @biomejs/biome@1.9.4 check $( (git diff --name-only; git ls-files --others --exclude-standard) | grep '^src/frontend/.*\.tsx\?$')`. Don't run `--write` on whole folders, because it converts untouched files to LF.
- Frontend: `cd src/frontend && yarn run extract && yarn run compile && yarn build`. The `tsc` step catches missing `ModelInformation` or renderer entries.
- Playwright: follow `F/tests/pages/pui_*.spec.ts` with `doCachedLogin`, and add `F/tests/pages/pui_fleet.spec.ts`.
  - **How P5 ran it locally (Windows):** `node_modules/.bin/playwright install chromium` once; a scratch SQLite database (`manage.py migrate` with `INVENTREE_DB_NAME` pointing into the scratchpad, `INVENTREE_DEBUG=True`, `INVENTREE_SITE_URL=http://localhost:8000`, plugins off) with the users `admin/inventree`, `allaccess/nolimits`, `reader/readonly`, `steven/wizardstaff` (superusers, **each with a `UserProfile`**, or the login answers 500) created in `manage.py shell`; `manage.py runserver 0.0.0.0:8000 --noreload` serving the built bundle (`vite build`); then `PLAYWRIGHT_BASE_URL=http://localhost:8000 playwright test tests/pages/pui_fleet.spec.ts -c <temporary config>` where the temporary config (deleted afterwards) is `playwright.config.ts` with `webServer: undefined`, Chromium only, 1 worker, no retries. Don't set `INVENTREE_COOKIE_SAMESITE=False` for a same-origin run: Chromium drops the `SameSite=None` session cookie on plain http.
  - **Local catalogs:** strings added since the last `lingui compile` show as message ids (e.g. "ASr+wT") in a local build. `extract` rewrites the tracked `.po` files, so run `lingui extract` + `lingui compile --typescript` in a scratch copy of `F/` (with a junction to `node_modules`) and copy back only the git-ignored `src/locales/*/messages.ts`. The image build does this by itself.
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

- **P1 changed no backend file outside `fleet/`.** Its new backend files are `fleet/helpers.py`, `fleet/signals.py`, `fleet/tasks.py`, `fleet/serializers.py`, `fleet/filters.py`, `fleet/services/**`, `fleet/management/**`, `fleet/migrations/0003_*` and the tests. They are all covered by the Dockerfile's whole-directory `COPY …/fleet/`, so the Dockerfile was not changed in P1.

- **P2 changed one backend file outside `fleet/`:** `common/setting/system.py` (the fleet URL validators and `_FLEET_POLL_STATE`). It is already in the Dockerfile overlay, and `fleet/` is copied whole, so the Dockerfile was not changed in P2.

- **P2b changed no backend file outside `fleet/`.**
  - New: `fleet/services/stock_sync.py`, `fleet/management/commands/fleet_sync_stock.py`, `fleet/test_stock_sync.py`.
  - Removed: `fleet/management/commands/fleet_import_existing.py`, `fleet/test_import.py`.
  - Changed: `fleet/{api,filters,serializers,tasks,test_api,test_pipeline}.py`, `fleet/services/{deployment,pipeline}.py`.
  - No migration: the deployment already had its own position fields.
  - The Dockerfile copies `fleet/` whole into a base image that has no `fleet/`, so removed files cannot linger. The Dockerfile was not changed in P2b.

- **P3 changed no backend file outside `fleet/`.**
  - New: `fleet/services/{planning,maintenance}.py`, `fleet/migrations/0004_p3_task_description.py`, `fleet/management/commands/fleet_install_reports.py`, `fleet/templates/fleet/fleet_service_report.html`, `fleet/test_maintenance.py`.
  - Changed: `fleet/{models,api,serializers,filters,tasks,test_pipeline}.py`.
  - The Dockerfile's whole-directory `COPY …/fleet/` covers the template and the command, so the Dockerfile was not changed in P3.

- **P4 changed no backend file outside `fleet/`.**
  - New: `fleet/services/trips.py`, `fleet/migrations/0005_p4_trip_kit_device.py`, `fleet/templates/fleet/fleet_trip_report.html`, `fleet/test_trips.py`.
  - Changed: `fleet/{models,api,serializers,filters,admin,test_maintenance}.py`, `fleet/services/{planning,maintenance,deployment,pipeline}.py`, `fleet/management/commands/fleet_install_reports.py`.
  - The Dockerfile was not changed in P4 (whole-directory `COPY …/fleet/`).

- **P5 changed four backend files outside `fleet/`** (all four are now in the Dockerfile overlay):
  - `web/urls.py` (`fleet/` route), `web/templatetags/spa_helper.py` (`entry`, `base_url`), `web/templates/web/fleet.html` (new), all CRLF in the working copy, kept.
  - **`InvenTree/middleware.py`** (CRLF, kept): `/fleet/` in `paths_own_security`, `fleet-portal` and `fleet-portal-root` in `pages_mfa_bypass`. It was not in the list above; see 5.1 for why it is needed.
  - New in `fleet/`: `test_portal.py`.

- **P7 changed no backend file outside `fleet/`.**
  - New: `fleet/services/device_state.py`, `fleet/migrations/0006_p7_device_state.py` (LF).
  - Changed: `fleet/{models,api,serializers,filters,admin,test_stock_sync,test_maintenance}.py` (LF), `fleet/test_monitoring.py` (CRLF kept), `fleet/services/{stock_sync,monitoring,planning,pipeline,deployment,maintenance}.py`.
  - The Dockerfile was not changed (whole-directory `COPY …/fleet/`); the overlay still equals the fork's backend diff minus tests.

- **P8 (conventions pass) changed one backend file outside `fleet/` for the image:** `InvenTree/api.py` (CRLF kept): the fleet models in the global search. **Added to the Dockerfile overlay** (fleet section). Test only: `generic/states/tests.py` (19 status classes now). New in `fleet/`: `events.py`, `migrations/0007_inventree_conventions.py`, `test_conventions.py`, `test_migrations.py`; the report template `fleet_trip_report.html` changed (responsible is an owner), so run `fleet_install_reports --update` after deploying.

**Fork frontend:**
- **Changed in P1:**
  - New: `src/fleet/{components,tables,panels}/**`, `src/pages/fleet/{FleetIndex,SiteDetail,DeploymentDetail,DeviceTypeDetail}.tsx`, `src/forms/FleetForms.tsx`, `src/components/render/Fleet.tsx`.
  - Modified: `lib/enums/{ModelType,ModelInformation,ApiEndpoints}.tsx`, `src/components/render/Instance.tsx`, `src/defaults/{backendMappings,links,actions}.tsx`, `src/components/nav/NavigationDrawer.tsx`, `src/functions/icons.tsx`, `src/router.tsx`, `src/pages/build/BuildDetail.tsx`, `src/pages/part/PartDetail.tsx`, `src/pages/Index/Settings/SystemSettings.tsx`.
- **Changed in P2:**
  - New: `src/fleet/components/{FleetMap,OverviewKpis,StreamStatusList}.tsx`, `src/fleet/tables/{AlertTable,DataStreamTable}.tsx`, `src/fleet/panels/{DeploymentHealthPanel,StockFleetPanel}.tsx`.
  - Modified: `lib/enums/{ModelType,ModelInformation,ApiEndpoints}.tsx`, `src/components/render/{Fleet,Instance}.tsx`, `src/defaults/{backendMappings,actions}.tsx`, `src/functions/icons.tsx`, `src/fleet/components/{FleetBadges,PipelineKpis}.tsx`, `src/forms/FleetForms.tsx`, `src/pages/fleet/{FleetIndex,DeploymentDetail,SiteDetail}.tsx`, `src/pages/stock/StockDetail.tsx`, `src/components/dashboard/DashboardWidgetLibrary.tsx`, `package.json` / `yarn.lock` (leaflet).
- **Changed in P2b:** `lib/enums/ApiEndpoints.tsx`, `src/forms/FleetForms.tsx`, `src/fleet/components/{FleetMap,OverviewKpis}.tsx`, `src/fleet/tables/DeploymentTable.tsx`, `src/pages/fleet/DeploymentDetail.tsx` (all LF).
- **Changed in P3:**
  - New (LF): `src/forms/ComponentServiceForms.tsx`, `src/fleet/components/{TaskExecution,ChecklistEditor,TaskCalendar}.tsx`, `src/fleet/tables/{TaskTable,ChecklistTemplateTable,KitTemplateTable,MaintenanceActionTable}.tsx`, `src/pages/fleet/TaskDetail.tsx`.
  - Modified, LF: `lib/enums/{ApiEndpoints,ModelType,ModelInformation}.tsx`, `src/defaults/backendMappings.tsx`, `src/components/render/{Fleet,Instance}.tsx`, `src/functions/icons.tsx`, `src/router.tsx`, `src/forms/FleetForms.tsx`, `src/fleet/components/{FleetBadges,PipelineKpis}.tsx`, `src/fleet/panels/{DeploymentHealthPanel,StockFleetPanel}.tsx`, `src/fleet/tables/AlertTable.tsx`, `src/pages/fleet/{FleetIndex,DeviceTypeDetail,DeploymentDetail,SiteDetail}.tsx`.
  - Modified, **CRLF kept**: `src/components/dashboard/DashboardWidgetLibrary.tsx` (P3 widgets), `src/tables/stock/InstalledItemsTable.tsx` (refactor only; its working copy had mixed endings, it is now all CRLF; `git diff` shows only the real changes because `core.autocrlf=true`).
- **Changed in P4** (all LF; the core files touched were already LF):
  - New: `src/fleet/components/{TripSelection,TeamSelect,TripStatusSteps,FleetCalendar}.tsx`, `src/fleet/tables/{TripTable,TripKitTable}.tsx`, `src/fleet/panels/TripReconcilePanel.tsx`, `src/pages/fleet/TripDetail.tsx`.
  - Modified: `lib/enums/{ModelType,ModelInformation,ApiEndpoints}.tsx`, `src/components/render/{Fleet,Instance}.tsx`, `src/defaults/backendMappings.tsx`, `src/functions/icons.tsx`, `src/router.tsx`, `src/forms/FleetForms.tsx`, `src/fleet/components/TaskExecution.tsx`, `src/fleet/tables/{TaskTable,DeploymentTable}.tsx`, `src/pages/fleet/{FleetIndex,TaskDetail}.tsx`.
- **Changed in P5:**
  - New (LF): `fleet.html`, `src/portal/**` (`main.tsx`, `PortalView.tsx`, `PortalRoutes.tsx`, `PortalLayout.tsx`, `navigation.tsx`, `useIsPhone.tsx`, `portal.css`, `components/{PortalPage,PortalCards,PortalTabs,PortalDetails}.tsx`, `pages/{Overview,Pipeline,Plan,Trips,TripDetail,MyWork,DeploymentDetail,TaskDetail,Alerts,Sites,SiteDetail}.tsx`), `src/fleet/hooks/{DeploymentActions,TaskActions,TripActions}.tsx`, `src/fleet/components/{ComponentTree,DeploymentTimeline}.tsx`, `tests/pages/pui_fleet.spec.ts`.
  - Modified, LF: `src/fleet/tables/DeploymentTable.tsx`, `src/forms/FleetForms.tsx`, `src/pages/fleet/{DeploymentDetail,TaskDetail,TripDetail}.tsx`.
  - Modified, **CRLF kept**: `vite.config.ts` (second entry, `fleetPortal()` plugin).
- **Changed in P7** (all LF): `lib/enums/ApiEndpoints.tsx` (`fleet_deployment_set_state`), `src/fleet/components/{FleetBadges,OverviewKpis,TaskExecution}.tsx`, `src/fleet/hooks/DeploymentActions.tsx`, `src/fleet/tables/DeploymentTable.tsx`, `src/forms/FleetForms.tsx`, `src/pages/fleet/DeploymentDetail.tsx`, `src/portal/pages/DeploymentDetail.tsx`, `tests/pages/pui_fleet.spec.ts`.
- **Changed in P8:** core `src/main.tsx` (CRLF kept; uses the new shared `src/functions/bootstrap.tsx`), `src/components/nav/SearchDrawer.tsx` (CRLF kept; fleet search entries), `lib/enums/ModelType.tsx` (`PluginPanelKey.fleet`), `src/router.tsx`; new `src/fleet/details/FleetDetailFields.tsx`, `tests/pages/pui_fleet_main.spec.ts`; many fleet and portal files (section 2.12).
- New: `F/fleet.html`, `F/src/portal/**`, `F/src/fleet/**`, `F/src/pages/fleet/**`, `F/src/forms/FleetForms.tsx`, `F/src/components/render/Fleet.tsx`.
- Modified: `vite.config.ts`, `router.tsx`, `defaults/links.tsx`, `defaults/actions.tsx`, `components/nav/NavigationDrawer.tsx`, `lib/enums/{ModelType,ModelInformation,ApiEndpoints,Roles}.tsx`, `components/render/Instance.tsx`, `defaults/backendMappings.tsx`, `functions/icons.tsx` (and `lib/types/Icons.tsx` if needed), `pages/stock/StockDetail.tsx`, `pages/build/BuildDetail.tsx`, `pages/part/PartDetail.tsx`, `tables/stock/InstalledItemsTable.tsx` (refactor only), `components/dashboard/DashboardWidgetLibrary.tsx`, `pages/Index/Settings/SystemSettings.tsx`, `package.json` / `yarn.lock` (leaflet).

**Deploy repo (`D/`):**
- **`Dockerfile`:** the backend overlay is a **hand-maintained list of individual files**, and must always equal `git diff --name-only 0a9a8b1c5 HEAD -- src/backend/InvenTree` (minus tests). Otherwise the image can contain a file that imports code which isn't copied, and the server crashes on start.
  - **Added in P0:** the whole `fleet/` directory, `InvenTree/settings.py`, `InvenTree/urls.py`, `users/oauth2_scopes.py`, `common/models.py` (`users/ruleset.py`, `stock/status_codes.py` and `common/setting/system.py` were already copied).
  - ~~**To add in P5:** `web/urls.py`, `web/templatetags/spa_helper.py`, `web/templates/web/fleet.html`.~~ **Added in P5:** those three and `InvenTree/middleware.py` (fleet section of the Dockerfile).
  - **Added in P5:** `templates/spa_fleet.html` (new in `D/`) is copied over `web/templates/web/fleet.html`, like `spa_index.html` over `index.html`: the portal page gets the BlueOasis favicon and theme stylesheet.

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

**As built in P6** (deploy repo only, all files CRLF as before; no fork code changed, so the Dockerfile overlay still equals the fork's backend diff minus tests and was not changed):
- `Caddyfile`: site block `https://:8444` with the same `tls /certs/cert.pem /certs/key.pem`, `redir / /fleet/ 302` (exact path `/` only; Caddy orders `redir` before the `route` of `common`) and `import common`, so `/static`, `/media` (with `forward_auth`), the plugin widget rewrite and the proxy are the same snippet on both ports. `/web/` also works on 8444.
- `docker-compose.yml`: proxy port `"8444:8444"`.
- `.env`: the three existing hosts (`192.168.1.153`, `100.66.76.10`, `ht-cop.taile85031.ts.net`) on `:8444` added to `INVENTREE_TRUSTED_ORIGINS` (product owner: same server and host names as InvenTree, nothing else opened). `.env.example`: placeholder line with both ports. Django's CSRF check compares the full origin including the port, so the `:8444` entries are required.
- `DEPLOY.md`: new section **Upgrading an existing server (Fleet module)**: backup → code (`git submodule update --init`, submodule at the fork commit) → `.env`/firewall → build → stop the worker and start db, cache, server, proxy → **one-off** `docker compose run --rm --no-deps inventree-server invoke migrate` + restart → `collect-static.sh` → `fleet_install_reports` → roles, settings, device types, `fleet_sync_stock --dry-run` → start the worker → verify; rollback; later upgrades. A `manage()` shell function wraps `docker compose exec inventree-server python3 src/backend/InvenTree/manage.py`. The new-server path points to steps 7–9.
- `docs/13-fleet.md` (new): key ideas (deployed = has a customer, statuses, optional site/position, health), managers (main UI panels, portal screens, device type setup, plan a deployment, plan maintenance and trips, kit, reconcile, reports, alerts), technicians (first time on the phone, certificate, "Barcode Scanner in Form Fields" under Settings → Display Options, My Work, task flow, photos as attachments, verify, complete), setup (roles table, settings table, device types, legacy widget kept).
- Also: `README.md` (guide rows, portal URL), `docs/README.md` (Fleet in the customizations, ports, docs index), `docs/04-deployment.md` (ports table), `docs/10-common-problems.md` (portal CSRF / INVE-E7, shared logout, portal static 404, INVE-W8 restart loop).
- Legacy *Deployed Systems* widget: **kept** (question 7 answered "keep it for now").

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

- (P1) Backend tests run with `USE_TZ = False` (`settings.py`: `USE_TZ = bool(not TESTING)`), so `timezone.now()` is naive in tests and `timezone.localtime()` raises. Use `fleet.helpers.to_local_date()`.
- (P1) `InvenTreeModelSerializer.__init__` fills missing fields with the **model defaults** before validation. So `'field' in self.initial_data` can't tell whether the client sent a field; check `self.context['request'].data` instead (see `DeploymentSerializer.create` and the coverage copy).
- (P1) The default permission chain includes `ModelPermission` (Django model perms: POST → `add_<model>`). Action endpoints that should need *change* must set their own `permission_classes` (see `FleetRoleMixin` in `fleet/api.py`, and `stock/components.py`).
- (P1) A related object cached on an in-memory instance (e.g. `dep.build`) does not see later saves through another instance. Refresh (`refresh_from_db()`) before computing risks in tests. The API always reads fresh rows.
- (P1) Build outputs for a trackable assembly need `create_build_output(1, serials=['…'])`, then `complete_build_output(output, user)`, then `complete_build(user)`. `complete_build` fires the Build `post_save` hook with status COMPLETE, which runs `sync_build_output`.

- (P2) The core `BaseURLValidator` is the site-URL validator and is locked to `SITE_URL`; don't use it for other URL settings. Django's plain `URLValidator` rejects host names without a TLD (`https://bo-server:8444`); `fleet.validators.OptionalURLValidator` allows them.
- (P2) `NotificationMessage` rows are only created when the plugin registry is loaded. Tests that check in-app notifications must call `self.ensurePluginsLoaded()` (as `build/test_build.py` does).
- (P2) `NotificationEntry.uid` is not nullable, so `trigger_notification(None, …)` fails after sending; `notify.send_in_app` skips notifications without an object.
- (P2b) A stock item can have a customer **and** be in a fleet pipeline deployment at the same time, but only when the customer was set outside the fleet app (by hand, or by shipping a sales order). The stock sync resolves this by moving the pipeline deployment to DEPLOYED. `deploy()` still refuses a device that already has a customer.
- (P2b) The `Build` `post_save` hook also fires when a pending BO is edited (e.g. its sales order is set). That is how a sales BO linked after creation gets its PLANNED deployment.
- (P2b) Docker Desktop is not always running on the dev machine; `local-inventree.ps1 test` then fails at once with "failed to connect to the docker API". Start Docker Desktop first. Run the whole suite with no other containers or builds running: many API tests check a 7.5 s per-request limit, and a stalled Docker VM makes a random one fail.
- (P2) In monitoring tests, stream ages are relative to a clock: when a poll runs at `now + N` minutes, report the stream times relative to the same moment (`report(..., at=N)` in `test_monitoring.py`), and remember that older data never moves `last_seen` backwards.

- (P3) InvenTree's OPTIONS metadata calls `model.get_api_url()` for every **writable** related field of a form serializer. A model without it only logs a warning (the field then cannot load options); a model whose `get_api_url()` reverses a URL that does not exist yet (`FieldTrip` -> `api-fleet-trip-list` until P4) makes the OPTIONS request fail, so the whole form fails. `test_maintenance.MaintenanceAPITest.test_options` loads every P3 form; extend it for P4.
- (P3) `InvenTreeAPITestCase.post()` expects **201** by default; fleet action endpoints answer 200, so pass `expected_code=200`.
- (P3) Related fields for models without a frontend `ModelType` (fault codes, checklist results, data streams) need a `modelRenderer` in the field definition, or they render as "unknown model".
- (P3) An `ApiForm` edit form fetches the instance and the fetched values override `initialData`. To pre-select a value (e.g. *Issue* on a checklist item), save it first and then open the form.
- (P3) **Line endings:** with `core.autocrlf=true`, core frontend files can be CRLF (or mixed) in the working copy while the repository stores LF. Git Bash's `grep $'\r'` does **not** find the CRs; count `b'\r\n'` in Python instead. Edit with a helper that converts to LF, applies the change and writes CRLF back. Biome reports every line of a CRLF file as a format error: check such a file through a temporary LF copy (then delete it) and never `--write` it.
- (P4) `InvenTreeModelSerializer.run_validation` builds a temporary model instance from the validated data on create. A writable many-to-many field (e.g. `FieldTrip.team`) or a write-only field that is not on the model (`tasks`, `deployments`) makes it fail with a 500 ("Direct assignment to the forward side of a many-to-many set"). List such fields in `skip_create_fields()`, as `FieldTripSerializer` does.
- (P4) `StockItem.move()` refuses items that are not "in stock", and a DAMAGED or DESTROYED component counts as not in stock. Reconcile moves removed components with `trips.move_removed()`, which falls back to setting the location directly and writes the same STOCK_MOVE entry.
- (P4) The trip `team` field is a list of users with no form field type in the frontend form system. `TeamSelect` is a separate multi-select, added to the request by hand.
- (P3) In this agent shell, bash heredocs that contain long Python with nested quotes sometimes fail ("unexpected EOF") or are only partly applied; write the script to the scratchpad and run it instead.

- (P5) **Never import `src/main.tsx` (or `router.tsx`, which lazily reaches pages that import it) from portal code.** `UserThemePanel` and `MobileAppView` import `IS_DEV` from `src/main.tsx`; once the portal graph reaches that module, Rollup turns the main entry into an empty facade, the manifest loses its `index.html` key and Django can no longer render the main UI. The portal imports the login pages directly for this reason, and the `fleetPortal()` Vite plugin fails the build when an entry goes missing.
- (P5) Django (`spa_bundle`) writes only the entry script and its dynamic imports, never the entry's **CSS** files. A stylesheet imported by an entry module itself is therefore never loaded in production (it works under `vite dev`). CSS imported by a lazily loaded module is fine: Vite preloads it with that chunk. `portal.css` is imported by `PortalView.tsx` for this reason.
- (P5) Edits with the editor tools and `sed -i` in Git Bash write LF: re-check CRLF files afterwards (P5 used a small script that converts to LF, applies exact replacements and writes CRLF back).
- (P5) Backend tests that GET a frontend page (`/web/`, `/fleet/`) fail with INVE-E7 (500) unless `SITE_URL` and `CSRF_TRUSTED_ORIGINS` match the test client host; use `override_settings(SITE_URL='http://testserver', CSRF_TRUSTED_ORIGINS=['http://testserver'])` as `InvenTree/test_middleware.py` does.
- (P5) The Mantine `AppShell` navbar collapsed on mobile is only moved off-screen (it stays in the DOM, focusable and "visible" to Playwright). The portal does not render the navbar on phones, nor the footer on larger screens.
- (P5) **Pre-existing, not fixed:** `src/states/GlobalStatusState.tsx` (fork code, "disassembly" status version) calls `fetchStatus()` from `onRehydrateStorage` while the store is created; depending on module order this reads `useUserState` before it is initialised and logs `ReferenceError: Cannot access '…' before initialization` on page load. It happens in the main UI built alone (checked with a single-entry build), is harmless (statuses are fetched again after login) and is not caused by P5. Fix later by deferring the call (e.g. `setTimeout`).

- (P6) With `INVENTREE_AUTO_UPDATE=False` the server container **exits on start while migrations are pending** (`INVE-W8: Database Migrations required`) and restart-loops, so `docker compose exec inventree-server invoke migrate` fails ("container is restarting"). Migrate with a one-off container (`docker compose run --rm --no-deps inventree-server invoke migrate`), then restart the server. Any upgrade that adds migrations hits this (the runbook says so).
- (P6) Docker Desktop: a Postgres data directory bind-mounted from a Windows path is unreliable; the staging override used a named volume.
- (P8) `StatusCode` members are **unhashable** (`BaseEnum` overrides `__eq__`), so they cannot be dict keys or set members: always use `.value` (as core does).
- (P8) `InvenTreeModel` already includes `PluginValidationMixin`; adding it again breaks the MRO.
- (P8) `fleet/integrations/base.py` already has a `StreamStatus` dataclass (platform data); the status class of a data stream is `DataStreamStatus`.
- (P8) `notify()` sends on commit. Django `TestCase` never commits, so tests which count Teams posts patch `fleet.notify.on_commit` to call at once (`MaintenanceDataMixin.configure`, `test_monitoring` setUp); `test_sent_on_commit` checks the real behaviour with `captureOnCommitCallbacks`.
- (P8) `required_alternate_scopes` lists are AND-ed inside and OR-ed outside: `[['r:change:fleet', 'r:change:stock']]` needs both scopes.

- (P6) **Pre-existing, not Fleet:** the worker logs `IntegrityError: null value in column "uid" of relation "common_notificationentry"` for key `update_available` (core `InvenTree/tasks.py` update check, unchanged in the fork). Harmless; the other tasks run.

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
5. ~~**Customer on deploy:** which company to use when a site has no client?~~ **Answered:** one default company via `FLEET_DEFAULT_CUSTOMER`. In P1, deploying fails with a clear message when neither the site client nor this setting is set.
6. **Default PM interval and checklist** per device type (Aurora PI5 75 m, Hydrophone 3 m, Aurora multichannel), and the standard kit per PM. *Partly answered:* the default is 180 days; per-type values, checklists and kits are still needed (as data) before P3. *Asked again at the start of P3 (2026-10-08), of P4 (2026-10-09), of P5 (2026-10-09) and of P6 (2026-10-09): "not yet, enter later".* `docs/13-fleet.md` explains how managers enter them. P3 was built so the lists are entered as data in the app (device type page: *Checklist* and *Parts Kit* panels); every type starts empty with 180 days. No code change is needed when the answer comes, only data (or a data migration if they want it seeded).
7. **Retire the legacy plugin's "Deployed Systems" widget** after P6? It also uses the customer convention, so it keeps working in the meantime. **Answered 2026-10-09: keep it for now**; retire later on request (remove it from the plugin and `docs/11-custom-widgets.md`).
8. ~~**Sales visibility (blocks the sales part of P2b):** which of (a), (b), (c) in section 2.10 does "every fleet device in a build order of a sale must be visible in the Fleet list" mean?~~ **Answered 2026-10-08: (b) only.** Pending fleet build orders linked to a sales order enter the pipeline when they are created. Built in P2b.
9. ~~**Internal device states**~~ **Answered 2026-10-09 (start of P7):** seven states, see section 2.11 (Active / Unresponsive automatic from data; Problem Acknowledged, Docked and Decommissioned set by technicians or managers; Maintenance Scheduled = a task on a field trip; Maintenance Overdue = PM past due, hidden while scheduled). Stored on the physical device (`DeviceLink`); Decommissioned survives the stock sync. Original question: **Internal device states (raised 2026-10-08; asked again at the start of P3, P4 and P5; at the start of P6 (2026-10-09) the product owner chose "later phase": a separate phase P7 with its own design):** the product owner wants an internal state per device in the Fleet list, for example "out of the water" or "ignore" (left out of the list, monitoring and planning). Today, RECOVERED already means "out of the water" for a closed deployment, and nothing marks a device as ignored: the stock sync would reopen a device that still has a customer. Before building, confirm the list of states, whether "ignore" must survive the stock sync (it must, or the sync re-adds the device), and which phase takes it.

## 13. Handoff: where to continue
Updated 2026-10-10, at the end of P8 (conventions pass, section 2.12). Everything below about P7 still holds; P8 adds migration `fleet.0007` (the runbook already migrates with the worker stopped), one more file in the Dockerfile overlay (`InvenTree/api.py`, done), and a changed trip report template: run `fleet_install_reports --update` after deploying. API consumers outside this repo (none known) would see integer `health`/`severity`/`state` and `*_detail` user objects instead of `*_name`. Open after P8: run the Playwright specs (including the new `pui_fleet_main.spec.ts`) against a server from the built image, and click through the main UI pages changed in P8.

**State of the work**
- P0 to P8 are implemented and verified (results in section 7). **Nothing is committed yet**: the fork's branch `blueoasis` has everything in its working tree, and the deploy repo has uncommitted P5 changes (`Dockerfile`, `templates/spa_fleet.html`), P6 changes (`Caddyfile`, `docker-compose.yml`, `.env`, `.env.example`, `DEPLOY.md`, `README.md`, `docs/README.md`, `docs/04-deployment.md`, `docs/10-common-problems.md`, new `docs/13-fleet.md`) and the P7 change to `docs/13-fleet.md`. Ask the product owner before committing, pushing or opening a PR.
- P7 adds migration `fleet.0006`: the go-live runbook (`D/DEPLOY.md`) already runs `invoke migrate` with the worker stopped, so nothing changes in it.
- **Staging stack left running on the dev PC** (P6 image, without P7; it was stopped during the P7 test run and started again) for a check on a real phone: `https://192.168.1.33:8444/` (LAN) or `https://100.65.8.32:8444/` / `https://drolo.taile85031.ts.net:8444/` (Tailscale), user `admin` / `inventree` (staging only, empty database plus the test data of the staging run). Files and `docker-compose.override.yml` are in the session scratchpad (`staging/`). Stop and remove it with `docker compose down -v` **in that scratch folder only** (project `inventree-p6-staging`, containers `p6stg-*`, image `blueoasis-inventree:p6-staging`); never in the deploy repo. Windows may ask to allow Docker through its firewall for the phone to connect.
- **Production is untouched.** To go live: commit the fork, point the deploy repo's submodule at that commit, commit the deploy repo, then follow `D/DEPLOY.md` → *Upgrading an existing server (Fleet module)* on the server (backup first; worker stopped until the stock-sync dry run has been reviewed).
- Still open:
  - The manual Teams card check from P2 needs a test channel webhook (cards `alert`, `pipeline_risk`, `task_completed`, `trip_kit_ready`).
  - Question 1 (data dashboard API spec): the HTTP client is still built on an assumed format; production stays on the `mock` provider (which reports every device healthy) until it is answered.
  - Question 6 is data only (entered in the app); question 7 answered "keep the widget for now".
  - The trip kit and reconcile flows have still not been clicked through in a browser (P4); the portal task flow was (P5, P6 staging), and the portal *Set State* flow by Playwright (P7). The main UI deployment page with the P7 state action was not opened in a browser (tsc only).
  - Known, pre-existing: `GlobalStatusState.tsx` "Cannot access … before initialization" (section 11); the core `update_available` notification error in the worker log (section 11).

**Next step: no phase is planned after P7.** Ask the product owner what comes next. Candidates:
- **Production go-live** (most likely): commit the fork (branch `blueoasis`) and the deploy repo, point the submodule at the fork commit, rebuild, and follow `D/DEPLOY.md` → *Upgrading an existing server (Fleet module)* on the server, with a backup first and the `fleet_sync_stock --dry-run` reviewed with the product owner before the worker starts. Every commit, push and server step needs the product owner's explicit go-ahead.
- **Real data API** (question 1): replace the assumed HTTP format in `integrations/http_client.py` (`parse_device()` and the request in `get_status()`) once the spec arrives.
- The open checks above (Teams card, trip kit/reconcile click-through, the P7 states on the main UI deployment page in a browser, the portal on a real phone).
- Same definition of done for any code phase (whole suite on Linux, Dockerfile overlay, image from a scratch copy).

**Starting prompt:** a ready-to-paste prompt for the next agent is in `dev/NEXT_PHASE_PROMPT.md`. It is local only: `dev/` is git-ignored. At the end of each phase, update it and this section for the phase after.
