import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    // Each worker owns a jsdom/React environment. Bound concurrent DOM suites
    // so larger CPU counts do not turn bounded async checks into timeouts.
    maxWorkers: 2,
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],
  }
})
