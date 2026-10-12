#!/bin/sh
# Run the backend test suite on Linux, the same way the upstream CI does.
# Used by `dev/local-inventree.ps1 test` inside the cached test image (dev/test-image).
#
# The repository is mounted read-only at /src-ro and copied to /work, so tests
# which write files (manifest, config, plugin folders) never touch the checkout.
set -e

# QUICK=1 (from `local-inventree.ps1 test -Quick`) skips the steps that only
# matter for a full run: collecting static files, compiling translations and
# the migration file check.
start=$(date +%s)
step() { echo "== $1 (+$(($(date +%s) - start))s)"; }

step 'Copying the source'
mkdir -p /work
cd /src-ro
tar --exclude=./.git --exclude=./dev --exclude=./.venv --exclude=./docs \
    --exclude=./src/frontend/node_modules --exclude='__pycache__' --exclude=./.pytest_cache \
    -cf - . | tar -xf - -C /work
cd /work

# Already in the cached test image (dev/test-image); only installed here for the bare upstream image
command -v msgfmt >/dev/null || (apt-get update -qq && apt-get install -y -qq gettext >/dev/null)

# Some tests read the git commit information
git init -q && git add -A 2>/dev/null && git -c user.name=test -c user.email=test@example.com commit -qm snapshot

step 'Checking dev requirements'
pip install --quiet --disable-pip-version-check -r src/backend/requirements-dev-3.14.txt

# The image bakes in production settings (commit hash, data paths, ...) which CI does not have
for var in $(env | sed -n 's/^\(INVENTREE_[A-Z_]*\)=.*/\1/p'); do
    unset "$var"
done

mkdir -p /tmp/t/media /tmp/t/static /tmp/t/backup
export INVENTREE_DB_ENGINE=sqlite3 \
    INVENTREE_MEDIA_ROOT=/tmp/t/media INVENTREE_STATIC_ROOT=/tmp/t/static INVENTREE_BACKUP_DIR=/tmp/t/backup \
    INVENTREE_DEBUG=True INVENTREE_SITE_URL=http://localhost:8000 \
    INVENTREE_PLUGINS_ENABLED=True INVENTREE_CONSOLE_LOG=False

# Collect static files using a throwaway database; the tests start from an empty one,
# because plugin and content type state in the main database leaks into some tests
# (skipped in quick mode: the quick labels do not need collected static files)
if [ "$QUICK" != 1 ]; then
    step 'Preparing (migrate, static files)'
    if ! { INVENTREE_DB_NAME=/tmp/t/prep.sqlite3 invoke migrate && INVENTREE_DB_NAME=/tmp/t/prep.sqlite3 invoke static; } > /tmp/t/prep.log 2>&1; then
        tail -40 /tmp/t/prep.log
        exit 1
    fi
fi

export INVENTREE_DB_NAME=/tmp/t/inventree.sqlite3

if [ "$QUICK" = 1 ]; then
    flags='--check'
else
    step 'Checking migration files'
    python3 .github/scripts/check_migration_files.py
    flags='--check --translations'
fi

step 'Running tests'
if [ -n "$TESTS" ]; then
    invoke dev.test $flags --runtest="$TESTS"
else
    invoke dev.test $flags
fi
step 'Done'
