/**
 * ESLint flat config（T5）——只做正确性 / 边界，不做全仓风格重排。
 *
 * 设计取舍：
 *   · src / tests 的既有代码大量使用 any（宿主 API 是动态边界），关闭 no-explicit-any；
 *   · 只启用 recommended 的正确性规则 + 模块边界规则；风格问题交给 Prettier；
 *   · runtime/*.mjs（历史运行时）与 lib/（构建产物）不在本门禁内，Phase R 迁移后另行收紧；
 *   · 入口与叶子层用 no-restricted-imports 钉死方向，环由 scripts/check-structure.mjs 兜底。
 */
import js from '@eslint/js'
import tseslint from 'typescript-eslint'

const TS_FILES = ['src/**/*.ts', 'tests/**/*.ts']
const MJS_FILES = ['scripts/**/*.mjs']

const noUnused = ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' }]

export default tseslint.config(
  {
    ignores: ['node_modules/**', 'lib/**', 'coverage/**', 'runtime/ext/**', 'skills/**', 'docs/**', '**/*.d.ts']
  },
  {
    files: TS_FILES,
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': noUnused,
      '@typescript-eslint/ban-ts-comment': 'off',
      '@typescript-eslint/no-require-imports': 'off',
      '@typescript-eslint/no-this-alias': 'off',
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-constant-condition': ['error', { checkLoops: false }],
      'no-control-regex': 'off',
      'no-useless-escape': 'off',
      'prefer-const': 'off',
      'no-undef': 'off'
    }
  },
  {
    files: MJS_FILES,
    extends: [js.configs.recommended],
    rules: {
      'no-undef': 'off',
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-unused-vars': noUnused,
      'no-useless-escape': 'off',
      'no-control-regex': 'off'
    }
  },
  {
    // runtime 已迁 TypeScript（T15）：比 src/tests 更严 —— 禁止显式 any 与 @ts-* 关闭注释。
    files: ['runtime/**/*.ts'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/ban-ts-comment': 'error',
      '@typescript-eslint/no-unused-vars': noUnused,
      '@typescript-eslint/no-require-imports': 'off',
      '@typescript-eslint/no-this-alias': 'off',
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-constant-condition': ['error', { checkLoops: false }],
      'no-control-regex': 'off',
      'no-useless-escape': 'off',
      'prefer-const': 'off',
      'no-undef': 'off'
    }
  },
  {
    // 入口层：只允许 re-export plugin 与 frame-cache 这两个公开面。
    files: ['src/index.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['./tools/*', './host/*', './plan/*', './headless/*', './ui/*', './contracts/*', './constants.js'],
              message: 'src/index.ts 是 entry-only：实现请下沉，入口只 re-export plugin 与 utils/frame-cache。'
            }
          ]
        }
      ]
    }
  },
  {
    // ui 是叶子渲染层，不得反向依赖业务层。
    files: ['src/ui/**/*.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../tools/*', '../plugin.js', '../headless/*', '../plan/*', '../host/*'],
              message: 'src/ui 是叶子层：只允许依赖 utils / contracts。'
            }
          ]
        }
      ]
    }
  },
  {
    // 基础契约层（constants / contracts / exec-context）不得依赖注册层，避免环。
    files: ['src/constants.ts', 'src/contracts/**/*.ts', 'src/host/exec-context.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../tools/*', '../plugin.js', '../headless/*', '../plan/*', '../../tools/*', '../../plugin.js'],
              message: '基础层不得依赖 tools / plugin / plan / headless。'
            }
          ]
        }
      ]
    }
  }
)
