import { expect, type Page, test } from "@playwright/test";

// goto resolves on the load event, which can come before React has hydrated the page (measured: up to 80 ms earlier).
// Text typed in that gap is wiped by hydration, so wait for the board, which only the hydrated page renders.
async function open(page: Page, url: string) {
  await page.goto(url);
  await expect(page.getByTestId("column-SAVED")).toBeVisible();
}

// Runs only through e2e/run-smoke.mjs, against a tracker database in a temporary directory.
test("add by URL, drag across a column, open the timeline, undo, pick a CV version", async ({ page, request }) => {
  const api = process.env.SMOKE_API_ORIGIN;
  expect(api, "start this with: npm run smoke").toBeTruthy();
  expect((await (await request.get(`${api}/api/v1/applications`)).json()).count).toBe(0);      // a fresh, disposable tracker

  await open(page, "/");
  await page.getByLabel("Job URL").fill("https://jobs.example.com/smoke/1?utm_source=newsletter");
  await page.getByLabel("Title").fill("Smoke Test Engineer");
  await page.getByLabel("Company").fill("Example Labs");
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("match")).toHaveAttribute("data-status", "none");
  expect((await (await request.get(`${api}/api/v1/applications`)).json()).count).toBe(0);      // the check stored nothing
  await page.getByRole("button", { name: "Save what I entered" }).click();
  await expect(page.getByRole("status")).toContainText("Added: Smoke Test Engineer at Example Labs.");

  const saved = page.getByTestId("column-SAVED");
  const applied = page.getByTestId("column-APPLIED");
  await expect(saved.getByRole("link", { name: "Smoke Test Engineer" })).toBeVisible();
  await saved.getByText("Example Labs").dragTo(applied);
  await expect(applied.getByRole("link", { name: "Smoke Test Engineer" })).toBeVisible();

  await applied.getByText("Example Labs").dragTo(saved);                                       // refused by the server
  await expect(page.getByRole("region", { name: "Applications by status" }).getByRole("alert")).toContainText("Applied can only move to: Online test, Interview, Offer");
  await expect(applied.getByRole("link", { name: "Smoke Test Engineer" })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("column-APPLIED").getByRole("link", { name: "Smoke Test Engineer" })).toBeVisible();

  await page.getByRole("link", { name: "Smoke Test Engineer" }).click();
  await expect(page.getByTestId("status")).toHaveText("Applied");
  const events = page.getByRole("region", { name: "Timeline" }).getByRole("listitem");
  await expect(events).toHaveCount(2);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByTestId("status")).toHaveText("Saved");
  await expect(events).toHaveCount(3);
  await expect(page.getByRole("button", { name: "Undo" })).toHaveCount(0);                      // the first event stays

  await page.getByLabel("New version label").fill("v1-smoke");
  await page.getByRole("button", { name: "Add version" }).click();
  await page.getByRole("combobox", { name: "CV version" }).selectOption({ label: "v1-smoke" });
  await expect(page.getByRole("combobox", { name: "CV version" }).locator("option:checked")).toHaveText("v1-smoke");
  await page.reload();
  await expect(page.getByRole("combobox", { name: "CV version" }).locator("option:checked")).toHaveText("v1-smoke");

  const stored = await (await request.get(`${api}/api/v1/applications`)).json();
  expect(stored.count).toBe(1);
  expect(stored.applications[0].status).toBe("SAVED");
  expect(stored.applications[0].cv_version_id).toBeTruthy();
});

// `next dev` prints the 127.0.0.1 address, but the API accepts changes only from the exact WEB_ORIGIN.
test("the 127.0.0.1 address lands on the exact origin, where a change is accepted", async ({ page, baseURL }) => {
  const site = new URL(baseURL!);
  await open(page, `http://127.0.0.1:${site.port}/`);
  await expect(page).toHaveURL(`${site.origin}/`);
  await page.getByLabel("Job URL").fill("https://jobs.example.com/smoke/origin");
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByTestId("match")).toHaveAttribute("data-status", "none");
  await expect(page.getByRole("region", { name: "Add an application" }).getByRole("alert")).toHaveCount(0);   // not Next's route announcer
});
