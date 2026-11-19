import { defineConfig } from "@playwright/test";

// The stack (API on :8001, frontend on :3001, isolated database `scriptorium_e2e`)
// is started by scripts/e2e.sh; tests talk to the real backend — no mocks.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3001",
    channel: process.env.E2E_BROWSER_CHANNEL ?? "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
});
