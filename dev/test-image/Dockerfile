# Cached image for the backend tests (dev/local-inventree.ps1 test).
# It is the upstream image plus everything dev/ci-test.sh used to install on
# every run, so each test run starts straight away.
#
# The script builds it when it is missing and rebuilds it when this file, the
# dev requirements or the base image change (it compares the "bo.test.hash"
# label). Force a rebuild with: dev\local-inventree.ps1 test -RebuildImage
ARG BASE_IMAGE=inventree/inventree:1.4.0
FROM ${BASE_IMAGE}

USER root

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends gettext git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-dev-3.14.txt /tmp/requirements-dev-3.14.txt
RUN pip install --quiet --disable-pip-version-check -r /tmp/requirements-dev-3.14.txt

ARG TEST_HASH=unknown
LABEL bo.test.hash=${TEST_HASH}
