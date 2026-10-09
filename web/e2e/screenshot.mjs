// Takes the README pictures (docs/images/) from synthetic data, through the smoke runner's disposable servers.
import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const images = path.resolve(here, "..", "..", "docs", "images");
mkdirSync(images, { recursive: true });
const run = spawnSync(process.execPath, [path.join(here, "run-smoke.mjs"), "e2e/screenshot.spec.ts"],
                      { stdio: "inherit", env: { ...process.env, SCREENSHOT_DIR: images } });
process.exit(run.status ?? 1);
