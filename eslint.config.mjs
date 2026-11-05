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
    '.dsh-ops/**',
    '.review-tmp/**',
    'runtime/ext/**',
    'skills/**',
    'docs/**',
    'addon/**',
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
    // Entry layer: only re-export plugin and the frame-cache public surface.
    files: ['src/index.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                './tools/*',
                './host/*',
                './plan/*',
                './headless/*',
                './ui/*',
                './contracts/*',
                './constants.js',
              ],
              message:
                'src/index.ts 是 entry-only：实现请下沉，入口只 re-export plugin 与 utils/frame-cache。',
            },
          ],
        },
      ],
    },
  },
  {
    // ui is a leaf rendering layer and must not depend on business modules.
    files: ['src/ui/**/*.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../tools/*', '../plugin.js', '../headless/*', '../plan/*', '../host/*'],
              message: 'src/ui 是叶子层：只允许依赖 utils / contracts。',
            },
          ],
        },
      ],
    },
  },
  {
    // Base contract layer must not depend on registration/dispatch layers.
    files: ['src/constants.ts', 'src/contracts/**/*.ts', 'src/host/exec-context.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '../tools/*',
                '../plugin.js',
                '../headless/*',
                '../plan/*',
                '../../tools/*',
                '../../plugin.js',
              ],
              message: '基础层不得依赖 tools / plugin / plan / headless。',
            },
          ],
        },
      ],
    },
  },
])
