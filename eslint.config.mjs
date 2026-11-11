/**
 * ESLint flat config (2026-10-31).
 *
 * Philosophy: catch real correctness problems, let Prettier own formatting.
 *   - TypeScript type-checking is handled by tsc; lint covers runtime hazards,
 *     unused code, unsafe directives and module-boundary rules.
 *   - Node globals come from the globals package instead of disabling no-undef.
 *   - Production code (src/ + runtime/) must not use explicit any; this is
 *     enforced here and independently by the TypeScript-AST gate in
 *     scripts/check-any.mjs. Tests may use any for doubles, so they get a
 *     narrow test-only override.
 *   - Boundary directions are enforced here; cycle detection stays in
 *     scripts/check-structure.mjs.
 */
import js from '@eslint/js'
import boundaries from 'eslint-plugin-boundaries'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

const TS_FILES = ['src/**/*.ts', 'runtime/**/*.ts', 'tests/**/*.ts']
const JS_FILES = ['scripts/**/*.mjs', '*.mjs']

const noUnused = [
  'error',
  { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
]

export default defineConfig([
  { linterOptions: { reportUnusedDisableDirectives: 'error' } },
  globalIgnores([
    'node_modules/**',
    'lib/**',
    'coverage/**',
    '.dsh/**',
    'local-workspace/**',
    '.review-tmp/**',
    'runtime/ext/**',
    'skills/**',
    'docs/**',
    'extension/**',
    '**/*.d.ts',
  ]),
  {
    files: JS_FILES,
    extends: [js.configs.recommended],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.node,
    },
    rules: {
      'no-unused-vars': noUnused,
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    files: TS_FILES,
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.node,
    },
    rules: {
      // TypeScript already resolves identifiers; no-undef would only create noise.
      'no-undef': 'off',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': noUnused,
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
  {
    // Tests need doubles for dynamic host services; keep any local to tests.
    files: ['tests/**/*.ts'],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
  {
    // Host-layer boundaries: frozen, explicitly allowed edges only. New files
    // and directories must be registered here; new cross-element edges must be
    // added to the policy list, never waived per file.
    files: ['src/**/*.ts'],
    plugins: { boundaries },
    settings: {
      'boundaries/elements': [
        { type: 'tools', pattern: 'src/tools' },
        { type: 'host', pattern: 'src/host' },
        { type: 'contracts', pattern: 'src/contracts' },
        { type: 'ui', pattern: 'src/ui' },
        { type: 'tui', pattern: 'src/tui' },
        { type: 'headless', pattern: 'src/headless' },
        { type: 'client', pattern: 'src/client' },
        { type: 'utils', pattern: 'src/utils' },
      ],
      'boundaries/files': [
        { pattern: 'src/index.ts', category: 'entry' },
        { pattern: 'src/constants.ts', category: 'constants' },
        { pattern: 'src/plugin*.ts', category: 'plugin' },
      ],
      'boundaries/ignore': ['lib/**'],
      'import/resolver': { typescript: { project: './tsconfig.json' } },
    },
    rules: {
      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          policies: [
            {
              from: { file: { categories: 'entry' } },
              allow: { to: { file: { categories: 'plugin' } } },
            },
            {
              from: { file: { categories: 'entry' } },
              allow: { to: { element: { types: ['utils'] } } },
            },
            {
              from: { element: { type: 'tools' } },
              allow: {
                to: { element: { types: ['constants', 'contracts', 'host', 'ui', 'utils'] } },
              },
            },
            {
              from: { element: { type: 'headless' } },
              allow: {
                to: { element: { types: ['constants', 'contracts', 'host', 'tools', 'ui'] } },
              },
            },
            {
              from: { element: { type: 'host' } },
              allow: { to: { element: { types: ['constants', 'contracts', 'tui'] } } },
            },
            {
              from: { element: { type: 'contracts' } },
              allow: { to: { element: { types: ['constants', 'host', 'ui'] } } },
            },
            {
              from: { element: { type: 'ui' } },
              allow: { to: { element: { types: ['contracts'] } } },
            },
            {
              from: { element: { type: 'tui' } },
              allow: { to: { element: { types: ['contracts'] } } },
            },
            {
              from: { element: { type: 'utils' } },
              allow: { to: { element: { types: ['contracts'] } } },
            },
            {
              from: { file: { categories: 'constants' } },
              allow: { to: { element: { types: ['contracts'] } } },
            },
            {
              from: { file: { categories: 'plugin' } },
              allow: {
                to: {
                  element: {
                    types: [
                      'constants',
                      'contracts',
                      'headless',
                      'host',
                      'tools',
                      'tui',
                      'ui',
                      'utils',
                    ],
                  },
                },
              },
            },
            {
              from: { file: { categories: 'plugin' } },
              allow: { to: { file: { categories: 'plugin' } } },
            },
            {
              from: { file: { categories: 'plugin' } },
              allow: { to: { file: { categories: 'constants' } } },
            },
            {
              from: { element: { type: 'tools' } },
              allow: { to: { file: { categories: 'constants' } } },
            },
            {
              from: { element: { type: 'headless' } },
              allow: { to: { file: { categories: 'constants' } } },
            },
            {
              from: { element: { type: 'host' } },
              allow: { to: { file: { categories: 'constants' } } },
            },
            {
              from: { element: { type: 'contracts' } },
              allow: { to: { file: { categories: 'constants' } } },
            },
          ],
        },
      ],
      'boundaries/no-unknown-files': ['error'],
    },
  },
  {
    // Runtime (backend) boundaries: directory elements plus one category for
    // the loose root modules. Every directory may import the root modules;
    // cross-directory edges are frozen to the measured matrix.
    files: ['runtime/**/*.ts'],
    plugins: { boundaries },
    settings: {
      'boundaries/elements': [
        { type: 'runtime-catalog', pattern: 'runtime/catalog' },
        { type: 'runtime-core', pattern: 'runtime/core' },
        { type: 'runtime-display', pattern: 'runtime/display' },
        { type: 'runtime-execution', pattern: 'runtime/execution' },
        { type: 'runtime-gateway', pattern: 'runtime/gateway' },
        { type: 'runtime-http', pattern: 'runtime/http' },
        { type: 'runtime-launch', pattern: 'runtime/launch' },
        { type: 'runtime-transport', pattern: 'runtime/transport' },
      ],
      'boundaries/files': [{ pattern: 'runtime/*.ts', category: 'runtime-root' }],
      'boundaries/ignore': ['lib/**'],
      'import/resolver': { typescript: { project: './runtime/tsconfig.json' } },
    },
    rules: {
      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          policies: [
            {
              from: { file: { categories: 'runtime-root' } },
              allow: {
                to: {
                  element: {
                    types: [
                      'runtime-catalog',
                      'runtime-core',
                      'runtime-display',
                      'runtime-execution',
                      'runtime-gateway',
                      'runtime-http',
                      'runtime-launch',
                      'runtime-transport',
                    ],
                  },
                },
              },
            },
            {
              from: { file: { categories: 'runtime-root' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-catalog' } },
              allow: { to: { element: { types: ['runtime-core'] } } },
            },
            {
              from: { element: { type: 'runtime-catalog' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-core' } },
              allow: {
                to: {
                  element: {
                    types: [
                      'runtime-catalog',
                      'runtime-display',
                      'runtime-execution',
                      'runtime-launch',
                      'runtime-transport',
                    ],
                  },
                },
              },
            },
            {
              from: { element: { type: 'runtime-core' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-display' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-execution' } },
              allow: { to: { element: { types: ['runtime-core', 'runtime-transport'] } } },
            },
            {
              from: { element: { type: 'runtime-execution' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-gateway' } },
              allow: {
                to: { element: { types: ['runtime-core', 'runtime-http', 'runtime-transport'] } },
              },
            },
            {
              from: { element: { type: 'runtime-gateway' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-http' } },
              allow: { to: { element: { types: ['runtime-catalog', 'runtime-core'] } } },
            },
            {
              from: { element: { type: 'runtime-http' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-launch' } },
              allow: { to: { element: { types: ['runtime-core'] } } },
            },
            {
              from: { element: { type: 'runtime-launch' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
            {
              from: { element: { type: 'runtime-transport' } },
              allow: { to: { file: { categories: 'runtime-root' } } },
            },
          ],
        },
      ],
      'boundaries/no-unknown-files': ['error'],
    },
  },
  {
    // src/client is a browser bundle source (CommonJS, no TypeScript project).
    // It still gets lint and the same default-deny boundary check.
    files: ['src/client/**/*.js'],
    extends: [js.configs.recommended],
    plugins: { boundaries },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'script',
      globals: { ...globals.browser, require: 'readonly' },
    },
    settings: {
      'boundaries/elements': [{ type: 'client', pattern: 'src/client' }],
    },
    rules: {
      'no-unused-vars': noUnused,
      'boundaries/dependencies': ['error', { default: 'disallow', policies: [] }],
      'boundaries/no-unknown-files': ['error'],
    },
  },
  {
    // Only src/host/runtime-bridge.ts may reach into the built runtime; every
    // other src file goes through host/backend or a contract.
    files: ['src/**/*.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                'lib/runtime/**',
                '../lib/runtime/**',
                '../../lib/runtime/**',
                '../../../lib/runtime/**',
              ],
              message: 'src 不得直接 import lib/runtime：只允许 src/host/runtime-bridge.ts。',
            },
          ],
        },
      ],
    },
  },
  {
    files: ['src/host/runtime-bridge.ts'],
    rules: {
      'no-restricted-imports': 'off',
    },
  },
])
