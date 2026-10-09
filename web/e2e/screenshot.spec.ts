import path from "node:path";
import { expect, test } from "@playwright/test";

// Takes the pictures used in the README, from synthetic applications only. Not part of the smoke run:
//   npm run screenshot        (writes docs/images/board.png)
// It runs through e2e/run-smoke.mjs, so the tracker database is a new one in a temporary directory.
const folder = process.env.SCREENSHOT_DIR;

const APPLICATIONS: { title: string; company: string; location: string; status: "SAVED" | "APPLIED"; then?: string[] }[] = [
  { title: "Junior Machine Learning Engineer", company: "Example AI Labs (synthetic)", location: "Bengaluru, India", status: "SAVED" },
  { title: "Computer Vision Intern", company: "Example Vision (synthetic)", location: "Chennai, India", status: "SAVED" },
  { title: "GenAI Engineer (Fresher)", company: "Example Retrieval Co (synthetic)", location: "Remote, India", status: "APPLIED" },
  { title: "Associate Software Engineer", company: "Example Fintech (synthetic)", location: "Gurugram, India", status: "APPLIED" },
  { title: "Python Backend Developer", company: "Example Software (synthetic)", location: "Pune, India", status: "APPLIED", then: ["online_test"] },
  { title: "Data Analyst - Graduate Trainee", company: "Example Analytics (synthetic)", location: "Hyderabad, India", status: "APPLIED", then: ["online_test", "interview"] },
  { title: "NLP Engineer", company: "Example NLP Studio (synthetic)", location: "Remote, India", status: "APPLIED", then: ["rejected"] },
];

test("README pictures from synthetic data", async ({ page, request }) => {
  test.skip(!folder, "run with: npm run screenshot");
  const api = process.env.SMOKE_API_ORIGIN!;
  const headers = (key: string) => ({ Origin: api, "Idempotency-Key": key });
  expect((await (await request.get(`${api}/api/v1/applications`)).json()).count).toBe(0);      // a fresh, disposable tracker
  for (const [index, item] of APPLICATIONS.entries()) {
    const created = await request.post(`${api}/api/v1/applications`, {
      headers: headers(`shot-${index}`),
      data: { title: item.title, company: item.company, location: item.location, status: item.status, source: "Company Radar",
              url: `https://jobs.example.com/demo/${1001 + index}` },
    });
    expect(created.status(), await created.text()).toBe(201);
    const id = (await created.json()).application.id;
    for (const [step, eventType] of (item.then ?? []).entries()) {
      const added = await request.post(`${api}/api/v1/applications/${id}/events`, { headers: headers(`shot-${index}-${step}`), data: { event_type: eventType } });
      expect(added.status(), await added.text()).toBe(201);
    }
  }

  await page.setViewportSize({ width: 1280, height: 640 });
  await page.goto("/app");
  await expect(page.getByTestId("column-INTERVIEW")).toContainText("Data Analyst");
  await page.screenshot({ path: path.join(folder!, "board.png") });

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const review = process.env.SCREENSHOT_REVIEW_DIR;                  // optional: full-page pictures for checking the design
  if (review) {
    await page.screenshot({ path: path.join(review, "landing-light.png"), fullPage: true });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.screenshot({ path: path.join(review, "landing-dark.png"), fullPage: true });
    await page.setViewportSize({ width: 360, height: 740 });
    await page.screenshot({ path: path.join(review, "landing-360-dark.png"), fullPage: true });
  }
});
