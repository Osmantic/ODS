import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './browser-tests',
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:18244' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 18244',
    url: 'http://127.0.0.1:18244',
    reuseExistingServer: false,
  },
})
