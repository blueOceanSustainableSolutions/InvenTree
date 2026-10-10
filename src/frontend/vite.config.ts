import { platform, release } from 'node:os';
import { fileURLToPath } from 'node:url';
import { codecovVitePlugin } from '@codecov/vite-plugin';
import { vanillaExtractPlugin } from '@vanilla-extract/vite-plugin';
import react from '@vitejs/plugin-react';
import license from 'rollup-plugin-license';
import { type Plugin, defineConfig } from 'vite';
import istanbul from 'vite-plugin-istanbul';

import { __INVENTREE_VERSION_INFO__ } from './version-info';

// Detect if the current environment is WSL
// Required for enabling file system polling
const IS_IN_WSL = platform().includes('WSL') || release().includes('WSL');

if (IS_IN_WSL) {
  console.debug('WSL detected: using polling for file system events');
}

// Output directory for the built files
const OUTPUT_DIR = '../../src/backend/InvenTree/web/static/web';

/**
 * Fleet Portal: the second entry (fleet.html -> src/portal/main.tsx).
 *
 * - "vite dev": serve fleet.html for /fleet/ paths (in production Django
 *   serves it, see web/templates/web/fleet.html).
 * - "vite build": fail if an entry is missing from the bundle. Django renders
 *   each entry from the manifest; when one entry imports a module of the
 *   other (e.g. src/main.tsx), Rollup merges it away and the page breaks.
 */
function fleetPortal(): Plugin {
  return {
    name: 'fleet-portal',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        if (req.url && /^\/fleet(\/|\?|$)/.test(req.url)) {
          req.url = '/fleet.html';
        }
        next();
      });
    },
    generateBundle(_options, bundle) {
      for (const name of ['index', 'fleet']) {
        const found = Object.values(bundle).some(
          (chunk) =>
            chunk.type === 'chunk' && chunk.isEntry && chunk.name === name
        );

        if (!found) {
          this.error(
            `Entry "${name}" is missing from the bundle: a module of one entry is imported by the other entry (e.g. src/main.tsx)`
          );
        }
      }
    }
  };
}

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
  // In 'build' mode, we want to use an empty base URL (for static file generation)
  const baseUrl: string | undefined = command === 'build' ? '' : undefined;

  return {
    plugins: [
      react({
        babel: {
          plugins: ['macros']
        }
      }),
      vanillaExtractPlugin(),
      fleetPortal(),
      license({
        sourcemap: true,
        thirdParty: {
          includePrivate: true,
          multipleVersions: true,
          output: {
            file: `${OUTPUT_DIR}/.vite/dependencies.json`,
            template(dependencies) {
              return JSON.stringify(dependencies);
            }
          }
        }
      }),
      istanbul({
        include: ['src/*', 'lib/*'],
        exclude: ['node_modules/', 'playwright/', 'tests/'],
        extension: ['.js', '.ts', '.tsx'],
        requireEnv: true
      }),
      codecovVitePlugin({
        enableBundleAnalysis: process.env.CODECOV_TOKEN !== undefined,
        bundleName: 'pui_v1',
        uploadToken: process.env.CODECOV_TOKEN
      })
    ],
    // When building, set the base path to an empty string
    // This is required to ensure that the static path prefix is observed
    base: baseUrl,
    build: {
      manifest: true,
      outDir: OUTPUT_DIR,
      sourcemap: true,
      rollupOptions: {
        // Two entries: the main UI and the Fleet Portal (served at /fleet/)
        input: {
          index: fileURLToPath(new URL('./index.html', import.meta.url)),
          fleet: fileURLToPath(new URL('./fleet.html', import.meta.url))
        }
      }
    },
    resolve: {
      alias: {
        '@lib': '/lib'
      }
    },
    server: {
      proxy: {
        '/media': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          secure: true
        },
        '/static': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          secure: true
        }
      },
      watch: {
        // Use polling only for WSL as the file system doesn't trigger notifications for Linux apps
        // Ref: https://github.com/vitejs/vite/issues/1153#issuecomment-785467271
        usePolling: IS_IN_WSL
      }
    },
    define: {
      ...__INVENTREE_VERSION_INFO__
    }
  };
});
