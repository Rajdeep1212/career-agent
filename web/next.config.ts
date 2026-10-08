import type { NextConfig } from "next";

// No usage data leaves the machine.
process.env.NEXT_TELEMETRY_DISABLED = "1";

// The browser talks only to this app; /api/v1 is passed on to the local FastAPI server. Nothing else is proxied.
const api = (process.env.TRACKER_API_ORIGIN ?? "http://localhost:8010").replace(/\/$/, "");
// The API accepts changes only from this exact origin (its WEB_ORIGIN). `next dev` prints the 127.0.0.1 address, where
// the board would load and every change would be refused, so any other host name is sent here.
const site = (process.env.WEB_ORIGIN ?? "http://localhost:3010").replace(/\/$/, "");

const config: NextConfig = {
  devIndicators: false,
  agentRules: false,
  distDir: process.env.NEXT_DIST_DIR ?? ".next", // the smoke run uses its own, beside a running dev server
  async redirects() {
    return [{ source: "/:path*", missing: [{ type: "host", value: new URL(site).hostname }], destination: `${site}/:path*`, permanent: false }];
  },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }];
  },
};

export default config;
