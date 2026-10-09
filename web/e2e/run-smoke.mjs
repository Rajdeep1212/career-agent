// One Playwright smoke run against a disposable tracker (docs/ROADMAP_QUEUE.md TRK3).
//
// Starts FastAPI on port 8011 with DATA_DIR pointing at a new temporary directory, and this app on port 3011 with
// /api/v1 passed on to it. data/tracker.sqlite3 and the other files under data/ are never opened: the run refuses to
// start if the temporary directory is inside the repository. Everything is stopped and removed afterwards.
//
// Arguments are passed on to Playwright, e.g. one spec file (npm run screenshot).
//
//   CAREER_AGENT_PYTHON       the app's Python (default: python on PATH)
//   PLAYWRIGHT_BROWSERS_PATH  where "npx playwright install chromium" put the browser
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = path.resolve(WEB, "..");
const API = "http://localhost:8011";
const SITE = "http://localhost:3011";
const dataDir = mkdtempSync(path.join(os.tmpdir(), "career-agent-smoke-"));
const fromRoot = path.relative(ROOT, dataDir);
if (!fromRoot.startsWith("..") && !path.isAbsolute(fromRoot)) {
  throw new Error(`The smoke data directory must be outside the repository: ${dataDir}`);
}

const children = [];
function start(command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stdout.on("data", (chunk) => { log = (log + chunk).slice(-4000); });
  child.stderr.on("data", (chunk) => { log = (log + chunk).slice(-4000); });
  child.on("error", (error) => { log += `\ncould not start ${command}: ${error.message}`; });
  children.push(child);
  return () => log;
}

function stopAll() {
  for (const child of children) {
    if (child.exitCode !== null || !child.pid) continue;
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    else child.kill("SIGTERM");
  }
}

async function ready(url, log, seconds = 180) {
  for (let waited = 0; waited < seconds; waited += 1) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`${url} did not answer in ${seconds} s.\n${log()}`);
}

let code = 1;
try {
  const apiLog = start(process.env.CAREER_AGENT_PYTHON ?? "python",
                       ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8011"], ROOT,
                       { DATA_DIR: dataDir, APP_ORIGIN: API, WEB_ORIGIN: SITE, DEMO_MODE: "false" });
  const webLog = start(process.execPath, [path.join(WEB, "node_modules", "next", "dist", "bin", "next"), "dev", "--hostname", "127.0.0.1", "--port", "3011"], WEB,
                       { TRACKER_API_ORIGIN: API, WEB_ORIGIN: SITE, NEXT_DIST_DIR: ".next-smoke", NEXT_TELEMETRY_DISABLED: "1" });
  await ready(`${API}/health`, apiLog);
  await ready(`${SITE}/`, webLog);
  const run = spawnSync(process.execPath, [path.join(WEB, "node_modules", "@playwright", "test", "cli.js"), "test", ...process.argv.slice(2)],
                        { cwd: WEB, stdio: "inherit", env: { ...process.env, SMOKE_WEB_ORIGIN: SITE, SMOKE_API_ORIGIN: API } });
  code = run.status ?? 1;
  if (code !== 0) console.error(`--- FastAPI ---\n${apiLog()}\n--- Next ---\n${webLog()}`);
} catch (error) {
  console.error(String(error?.message ?? error));
} finally {
  stopAll();
  await new Promise((resolve) => setTimeout(resolve, 1500));
  try {
    rmSync(dataDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
  } catch {
    console.error(`Could not remove ${dataDir}; it is safe to delete.`);
  }
}
console.log(code === 0 ? `smoke: passed (disposable data in ${dataDir}, removed)` : "smoke: FAILED");
process.exit(code);
