import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    include: [
      'tests/unit/**/*.test.ts',
      'tests/integration/**/*.test.ts',
    ],
    exclude: ['tests/e2e/**', 'tests/**/*.selftest.mjs', 'tests/**/*_selftest.*'],
    environment: 'node',
    testTimeout: 20_000,
    hookTimeout: 20_000,
    globalSetup: ['./tests/helpers/global-setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],
      include: ['src/**/*.ts', 'runtime/**/*.ts'],
      exclude: ['lib/**', 'tests/**', 'runtime/**/*.py', 'runtime/**/*.d.ts'],
    },
  },
})
