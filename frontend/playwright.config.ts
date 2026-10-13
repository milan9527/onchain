import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: process.env.TEST_BASE_URL ? "test-results-cloud" : "test-results",
  timeout: 240_000,
  expect: { timeout: 25_000 },
  use: {
    actionTimeout: 25_000,
    baseURL: process.env.TEST_BASE_URL || "http://localhost:5173",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  workers: 1,
});
