"""Tests for serving the Fleet Portal (second frontend entry, phase P5)."""

import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from InvenTree.unit_test import InvenTreeTestCase
from web.templatetags import spa_helper

# A manifest with both Vite entries, as `yarn build` writes it
MANIFEST = {
    'index.html': {
        'file': 'assets/index-main.js',
        'isEntry': True,
        'dynamicImports': ['_DesktopAppView.js'],
    },
    'fleet.html': {
        'file': 'assets/fleet-portal.js',
        'isEntry': True,
        'dynamicImports': ['_PortalApp.js'],
    },
    '_DesktopAppView.js': {'file': 'assets/DesktopAppView.js'},
    '_PortalApp.js': {'file': 'assets/PortalApp.js'},
}


def settings_json(html: str) -> dict:
    """Return the INVENTREE_SETTINGS object rendered by spa_settings."""
    start = html.index('window.INVENTREE_SETTINGS=') + len('window.INVENTREE_SETTINGS=')
    end = html.index('</script>', start)
    return json.loads(html[start:end])


class SpaHelperEntryTest(TestCase):
    """The template tags render the entry and settings of the portal."""

    def setUp(self):
        """Write a manifest with both entries to a temporary file."""
        super().setUp()
        self.folder = tempfile.TemporaryDirectory()
        self.manifest = Path(self.folder.name) / 'manifest.json'
        self.manifest.write_text(json.dumps(MANIFEST))

    def tearDown(self):
        """Remove the temporary manifest."""
        self.folder.cleanup()
        super().tearDown()

    def test_default_entry(self):
        """Without an entry, the main index.html bundle is rendered."""
        html = spa_helper.spa_bundle(self.manifest)

        self.assertIn('web/assets/index-main.js', html)
        self.assertIn('web/assets/DesktopAppView.js', html)
        self.assertNotIn('fleet-portal.js', html)

    def test_fleet_entry(self):
        """entry='fleet.html' renders the portal bundle only."""
        html = spa_helper.spa_bundle(self.manifest, entry='fleet.html')

        self.assertIn(f'{settings.STATIC_URL}web/assets/fleet-portal.js', html)
        self.assertIn('web/assets/PortalApp.js', html)
        self.assertNotIn('index-main.js', html)

    def test_missing_entry(self):
        """A build without the portal entry reports NOT_FOUND (INVE-E1 page)."""
        manifest = dict(MANIFEST)
        del manifest['fleet.html']
        self.manifest.write_text(json.dumps(manifest))

        self.assertEqual(
            spa_helper.spa_bundle(self.manifest, entry='fleet.html'), 'NOT_FOUND'
        )
        self.assertIn('index-main.js', spa_helper.spa_bundle(self.manifest))

    def test_settings_base_url(self):
        """spa_settings(base_url=...) overrides the base url for the portal."""
        default = settings_json(spa_helper.spa_settings())
        self.assertEqual(default['base_url'], settings.FRONTEND_URL_BASE)
        self.assertNotIn('main_base_url', default)

        portal = settings_json(spa_helper.spa_settings(base_url='fleet'))
        self.assertEqual(portal['base_url'], 'fleet')
        self.assertEqual(portal['main_base_url'], settings.FRONTEND_URL_BASE)

        # The other settings are unchanged
        for key in ['server_list', 'show_server_selector', 'environment']:
            self.assertEqual(portal[key], default[key])

        # The module-level settings are not modified
        self.assertEqual(
            settings.FRONTEND_SETTINGS['base_url'], settings.FRONTEND_URL_BASE
        )


@override_settings(
    SITE_URL='http://testserver', CSRF_TRUSTED_ORIGINS=['http://testserver']
)
class PortalViewTest(InvenTreeTestCase):
    """The /fleet/ path serves the portal page."""

    def check_portal(self, response):
        """The response is the portal page with the portal settings."""
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(settings_json(html)['base_url'], 'fleet')
        self.assertIn('csrftoken', response.cookies)

    def test_portal_paths(self):
        """Every path below /fleet/ is the portal (client side routes)."""
        self.assertEqual(reverse('fleet-portal'), '/fleet/')

        for path in ['/fleet/', '/fleet/trips/3', '/fleet/task/12/', '/fleet/my']:
            self.check_portal(self.client.get(path))

    def test_anonymous(self):
        """The portal handles its own login: no redirect to the main UI."""
        self.client.logout()

        self.check_portal(self.client.get('/fleet/'))
        self.check_portal(self.client.get('/fleet/deployment/4'))

        response = self.client.get('/fleet')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/fleet/')

    def test_main_ui_unchanged(self):
        """The main UI keeps its own entry and base url."""
        response = self.client.get(f'/{settings.FRONTEND_URL_BASE}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            settings_json(response.content.decode())['base_url'],
            settings.FRONTEND_URL_BASE,
        )

    def test_portal_port(self):
        """The portal port (8444) of the same host passes the host checks.

        The proxy serves the portal on its own port. With the default lax
        check the same host name is enough; with the strict check the portal
        origin must be in the trusted origins (INVE-E7 otherwise).
        """
        headers = {'host': 'testserver:8444'}

        with self.settings(
            SITE_URL='http://testserver:8443',
            CSRF_TRUSTED_ORIGINS=['http://testserver:8443'],
            SITE_LAX_PROTOCOL_CHECK=True,
        ):
            self.check_portal(self.client.get('/fleet/', headers=headers))

        with self.settings(
            SITE_URL='http://testserver:8443',
            CSRF_TRUSTED_ORIGINS=['http://testserver:8443'],
            SITE_LAX_PROTOCOL_CHECK=False,
        ):
            response = self.client.get('/fleet/', headers=headers)
            self.assertContains(response, 'INVE-E7', status_code=500)

        with self.settings(
            SITE_URL='http://testserver:8443',
            CSRF_TRUSTED_ORIGINS=['http://testserver:8443', 'http://testserver:8444'],
            SITE_LAX_PROTOCOL_CHECK=False,
        ):
            self.check_portal(self.client.get('/fleet/', headers=headers))

    def test_api_not_shadowed(self):
        """The fleet API is still served under /api/fleet/."""
        response = self.client.get('/api/fleet/overview/')
        self.assertNotEqual(response.status_code, 404)
