import { defineConfig, devices } from "@playwright/test";

const API_ORIGIN = process.env.E2E_API_ORIGIN || "http://localhost:8000";
const BASE_URL = process.env.E2E_BASE_URL || "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  // Raised for the cursor-pagination test, which walks several pages of a
  // 5,000-message mailbox. Set globally rather than per-test: the per-test
  // options overload is shadowed by the extended fixture type in e2e/fixtures.
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "tablet", use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 } } },
    { name: "mobile", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } } },
  ],
  metadata: { apiOrigin: API_ORIGIN },
});