import type { MetadataRoute } from "next";

// The installed app opens the tracker board, on the exact origin the API accepts changes from (WEB_ORIGIN), never on 127.0.0.1.
const site = (process.env.WEB_ORIGIN ?? "http://localhost:3010").replace(/\/$/, "");

// Built once, so the manifest is also valid in a static export.
export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Career Agent",
    short_name: "Career Agent",
    description: "Where each application stands. Runs on this computer only.",
    id: `${site}/`,
    start_url: `${site}/app`,
    scope: `${site}/`,
    display: "standalone",
    background_color: "#f8f7f4",
    theme_color: "#2b5cab",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
