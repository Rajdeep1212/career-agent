import type { NextConfig } from "next";

// No usage data leaves the machine.
process.env.NEXT_TELEMETRY_DISABLED = "1";

// The browser talks only to this app; /api/v1 is passed on to the local FastAPI server. Nothing else is proxied.
const api = (process.env.TRACKER_API_ORIGIN ?? "http://localhost:8010").replace(/\/$/, "");

const config: NextConfig = {
  devIndicators: false,
  agentRules: false,
  distDir: process.env.NEXT_DIST_DIR ?? ".next", // the smoke run uses its own, beside a running dev server
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }];
  },
};

export default config;
