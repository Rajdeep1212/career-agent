import { defineConfig } from "@playwright/test";

// Started by e2e/run-smoke.mjs, which brings up FastAPI on a disposable data directory and this app beside it.
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  workers: 1,
  reporter: "line",
  use: { baseURL: process.env.SMOKE_WEB_ORIGIN ?? "http://localhost:3011", headless: true },
});
