import { expect, type Page, test } from "@playwright/test";

// Runs only through e2e/run-smoke.mjs. The landing page is static: it needs no API.

// The lowest contrast ratio between any visible text and what is painted behind it. Colours are read by painting them
// on a canvas, so oklch() and partly transparent colours are measured as the browser draws them.
async function lowestContrast(page: Page) {
  return page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true })!;
    const paint = (colours: string[]) => {
      context.clearRect(0, 0, 1, 1);
      for (const colour of colours) {
        context.fillStyle = colour;
        context.fillRect(0, 0, 1, 1);
      }
      return [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
    };
    const luminance = (rgb: number[]) => {
      const [r, g, b] = rgb.map((value) => (value / 255 <= 0.04045 ? value / 255 / 12.92 : ((value / 255 + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const backgrounds = (element: Element) => {
      const layers: string[] = [];
      for (let node: Element | null = element; node; node = node.parentElement) layers.unshift(getComputedStyle(node).backgroundColor);
      return ["#ffffff", ...layers];
    };
    let lowest = { ratio: 99, text: "", size: 0 };
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const element = node.parentElement!;
      const text = (node.textContent ?? "").trim();
      const style = getComputedStyle(element);
      if (!text || element.closest("svg, .sr-only") || style.visibility === "hidden" || element.getClientRects().length === 0) continue;
      const behind = paint(backgrounds(element));
      const front = paint([`rgb(${behind.join(",")})`, style.color]);
      const [light, dark] = [luminance(front), luminance(behind)].sort((a, b) => b - a);
      const ratio = (light + 0.05) / (dark + 0.05);
      if (ratio < lowest.ratio) lowest = { ratio, text: text.slice(0, 60), size: parseFloat(style.fontSize) };
    }
    return lowest;
  });
}

test("the landing page loads with no console error and nothing from another site", async ({ page, context, baseURL }) => {
  const problems: string[] = [];
  const outside: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") problems.push(message.text()); });
  page.on("pageerror", (error) => problems.push(String(error)));
  page.on("request", (request) => { if (!request.url().startsWith(baseURL!) && !request.url().startsWith("data:")) outside.push(request.url()); });

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Evidence-first job search for Indian freshers");
  await expect(page.getByRole("img", { name: /How Career Agent works/ })).toBeVisible();
  await expect(page.getByText("Try the demo: coming soon")).toBeVisible();
  await expect(page.getByTestId("result-gold")).toContainText("106 jobs");
  await page.waitForLoadState("networkidle");

  expect(problems).toEqual([]);
  expect(outside).toEqual([]);
  expect(await context.cookies()).toEqual([]);
  expect(await page.evaluate(() => Object.keys(localStorage).length + Object.keys(sessionStorage).length)).toBe(0);
});

test("the landing page carries the social tags and an image under 200 KB", async ({ page, request }) => {
  await page.goto("/");
  const meta = (name: string) => page.locator(`meta[property="${name}"], meta[name="${name}"]`).first().getAttribute("content");
  expect(await meta("og:title")).toContain("Career Agent");
  expect(await meta("og:description")).toBeTruthy();
  expect(await meta("twitter:card")).toBe("summary_large_image");
  const image = await meta("og:image");
  expect(await meta("og:image:width")).toBe("1200");
  expect(await meta("og:image:height")).toBe("630");
  const picture = await request.get(new URL(image!).pathname);
  expect(picture.headers()["content-type"]).toBe("image/png");
  expect((await picture.body()).length).toBeLessThan(200 * 1024);
});

test("the landing page fits a 360 px screen and can be used from the keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/");
  // Only the diagram scrolls sideways, inside its own box; the page itself does not.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to the content" })).toBeFocused();
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle);
  expect(outline).not.toBe("none");
});

for (const scheme of ["light", "dark"] as const) {
  test(`the landing page's text meets WCAG AA contrast in ${scheme} mode`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/");
    const lowest = await lowestContrast(page);
    expect(lowest.ratio, `"${lowest.text}" (${lowest.size}px)`).toBeGreaterThanOrEqual(4.5);
  });
}

test("the installed app's start address opens the tracker board", async ({ page, request, baseURL }) => {
  const manifestLink = await (await request.get("/")).text();
  expect(manifestLink).toContain('rel="manifest"');
  const manifest = await (await request.get("/manifest.webmanifest")).json();
  expect(new URL(manifest.start_url).pathname).toBe("/app");
  await page.goto(new URL(manifest.start_url).pathname, { waitUntil: "load" });
  await expect(page).toHaveURL(`${baseURL}/app`);
  await expect(page.getByTestId("column-SAVED")).toBeVisible();
});
