import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

// The address this build is published at: the public site when NEXT_PUBLIC_SITE_URL is set, otherwise the local app.
const site = (process.env.NEXT_PUBLIC_SITE_URL ?? process.env.WEB_ORIGIN ?? "http://localhost:3010").replace(/\/$/, "");
const title = "Career Agent: evidence-first job search for Indian freshers";
const description = "Finds jobs on official company boards, checks eligibility with the listing quoted, ranks them honestly and tracks your applications. Local-first and open source.";
const image = { url: "/og.png", width: 1200, height: 630, alt: "Career Agent: evidence-first job search for Indian freshers" };

export const metadata: Metadata = {
  metadataBase: new URL(site),
  title,
  description,
  applicationName: "Career Agent",
  openGraph: { type: "website", siteName: "Career Agent", title, description, url: "/", images: [image] },
  twitter: { card: "summary_large_image", title, description, images: [image] },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
