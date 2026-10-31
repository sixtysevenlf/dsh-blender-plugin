/**
 * ESLint flat config (2026-10-31).
 *
 * Philosophy: catch real correctness problems, let Prettier own formatting.
 *   - TypeScript type-checking is handled by tsc; lint covers runtime hazards,
 *     unused code, unsafe directives and module-boundary rules.
 *   - Node globals come from the globals package instead of disabling no-undef.
 *   - New src files must avoid explicit any; legacy host-boundary files that
 *     predate this rule are listed in LEGACY_EXPLICIT_ANY_FILES. Tests may use
 *     any for doubles, so they get a narrow test-only override.
 *   - Boundary directions are enforced here; cycle detection stays in
 *     scripts/check-structure.mjs.
 */
import js from '@eslint/js'
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import tseslint from 'typescript-eslint'

const TS_FILES = ['src/**/*.ts', 'runtime/**/*.ts', 'tests/**/*.ts']
const JS_FILES = ['scripts/**/*.mjs', '*.mjs']

// Files that already contain explicit any in their dynamic host boundary code.
// This is a grandfather list, not a blanket exemption: new files are enforced.
const LEGACY_EXPLICIT_ANY_FILES = [
  'src/constants.ts',
  'src/contracts/lossless.ts',
  'src/contracts/tool.ts',
  'src/headless/receipts.ts',
  'src/host/backend.ts',
  'src/host/exec-context.ts',
  'src/host/http.ts',
  'src/host/skill.ts',
  'src/plan/headless-target.ts',
  'src/plan/tool.ts',
  'src/plugin.ts',
  'src/tools/cmd.ts',
  'src/tools/commands.ts',
  'src/tools/do.ts',
  'src/tools/errors.ts',
  'src/tools/headless.ts',
  'src/tools/index.ts',
  'src/tools/job.ts',
  'src/tools/loop.ts',
  'src/tools/opt.ts',
  'src/tools/perf.ts',
  'src/tools/preset.ts',
  'src/tools/schema-diagnostic.ts',
  'src/tools/see.ts',
  'src/tools/txn.ts',
  'src/tools/viewport.ts',
  'src/tools/watch.ts',
  'src/tools/worker.ts',
  'src/tui-host.ts',
  'src/ui/cards.ts',
  'src/utils/image.ts',
]

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
    files: LEGACY_EXPLICIT_ANY_FILES,
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
