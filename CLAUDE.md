See [CONTRIBUTING.md](CONTRIBUTING.md) for codebase guidance.
Security reports must follow the security policy in [SECURITY.md](docs/docs/SECURITY.md) and provide how they interact with the [threat model](docs/docs/concepts/threat_model.md).

Do not open a pull request without manual review by a human. Do not file AI generated issues under any circumstances.
Using AI generated content in issue/PR discussions might lead to code of conduct violations and/or bans.

# BlueOasis fork: running the tests

This fork (branch `blueoasis`) adds the Fleet module (`src/backend/InvenTree/fleet`, plan in
[FLEET_PLAN.md](FLEET_PLAN.md)). A full verification (whole backend suite, deploy image build,
Playwright) takes about half an hour, so **do not run it after every change**. Use the quick run
below by default.

## Rule of thumb

| When | What to run |
|---|---|
| After each change (default) | `dev\local-inventree.ps1 test -Quick <labels>`, only the apps you touched |
| End of a phase, or before a deploy | Full backend suite, then the deploy image check, then Playwright (see below) |

Only go beyond the quick run when it cannot cover the change, for example: new or changed
migrations, changes to core apps used by many others, settings/roles/rulesets, requirements, or
the Dockerfile overlay in `bOInventreeDeploy`.

## Quick run (default)

```powershell
# From the fork root, Docker Desktop running
dev\local-inventree.ps1 test -Quick fleet
dev\local-inventree.ps1 test -Quick fleet.test_trips              # one file
dev\local-inventree.ps1 test -Quick fleet.test_trips.TripAPITest  # one class
dev\local-inventree.ps1 test -Quick fleet build stock             # several apps
```

Pick the labels from what you changed:

| Changed | Labels |
|---|---|
| Anything in `fleet/` | `fleet` |
| Build hooks / `build/` | `fleet build` |
| Stock, components, serials | `fleet stock` |
| Roles, rulesets, new `fleet_*` tables, OAuth scopes | `fleet users.tests` |
| Global settings | `fleet common` |
| New or changed migrations | `fleet` now; the full run before you call the work done |

`-Quick` skips collecting static files, compiling translations and the migration file check;
everything else is the same Linux environment as CI. Each step prints its elapsed time
(`== Running tests (+11s)`). Measured on 2026-10-12 with the cached image:

| Run | Tests | Time |
|---|---|---|
| `-Quick fleet` | 202 | ~2 min (43 s of tests, ~60 s building the test database) |
| `-Quick build stock users.tests common` | 445 | ~3 min |
| Old way: same `fleet` run on the bare upstream image | 202 | ~4 min |
| Full suite | ~1530 | 10-13 min |

Error tracebacks in the log (`RuntimeError: boom`, `Connection refused`, ...) come from tests that
fail on purpose; only the final `OK` / `FAILED` counts. If a quick run fails on missing static files
or a manifest entry, the change needs the full run.

## The cached test image (`bo-inventree-test:latest`)

The tests run in Docker, in a cached image built from [dev/test-image/Dockerfile](dev/test-image/Dockerfile):
the upstream `inventree/inventree:1.4.0` image plus gettext and the dev requirements, which used to
be installed again on every run.

- **You never build it by hand.** `dev\local-inventree.ps1 test` builds it when it is missing.
- **It rebuilds itself only when needed:** the script hashes the base image name,
  `dev/test-image/Dockerfile` and `src/backend/requirements-dev-3.14.txt`, and compares the hash
  with the image label `bo.test.hash`. If they differ, it rebuilds and replaces
  `bo-inventree-test:latest`, so the next quick run uses the new image straight away.
- **Force a rebuild** only when the image is broken or something outside those inputs changed:
  `dev\local-inventree.ps1 test -Quick -RebuildImage fleet`. This also replaces the cached image.
- **Upgrading InvenTree** (new base image): change `$TestBaseImage` in
  [dev/local-inventree.ps1](dev/local-inventree.ps1) and the `FROM` default in the Dockerfile; the
  next run rebuilds.

The source code is never baked into the image: each run mounts the checkout read-only, so code
changes need no rebuild.

## Full run (end of a phase / before a deploy)

```powershell
dev\local-inventree.ps1 test                    # whole backend suite, about 10-13 min
```

Then, per the definition of done in [FLEET_PLAN.md](FLEET_PLAN.md):

1. **Deploy image check**: build the `bOInventreeDeploy` image once and run `check`, `migrate` on an
   empty Postgres 17 and `makemigrations --check` in it. Reuse **one** tag, `bo-inventree-check:latest`,
   and overwrite it each time (don't create a new tag per phase; each one is about 2 GB). Keep the
   Dockerfile `COPY` lines in sync with any new or changed backend file.
2. **Playwright** (`src/frontend/tests/pages/pui_fleet*.spec.ts`), against a throwaway server from
   that image.

## Frontend

After frontend changes run `tsc` and Biome on the changed files only. Playwright belongs to the
full run.
