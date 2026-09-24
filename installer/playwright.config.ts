import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.browser.ts",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:18243" },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 18243",
    url: "http://127.0.0.1:18243",
    reuseExistingServer: false,
  },
});
