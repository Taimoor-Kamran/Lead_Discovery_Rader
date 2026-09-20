import { defineConfig, devices } from "@playwright/test";

/**
 * `make e2e`: the smoke test against the running compose stack with demo data and demo
 * users. Not part of `make check`. Needs `DEMO_USERS_PASSWORD` (from `.env`) and a stack
 * that `make up && make migrate && make seed-admin && make seed-demo-users && make
 * load-demo-data` prepared.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } } }],
});
