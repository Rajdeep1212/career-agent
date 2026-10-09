import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Application tracker", description: "Career Agent: where each application stands." };

export default function TrackerLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-[1500px] items-baseline gap-3 px-4 py-3">
          <a href="/app" className="text-base font-semibold tracking-tight">Application tracker</a>
          <span className="text-sm text-muted">Career Agent, on this computer</span>
        </div>
      </header>
      <main className="mx-auto max-w-[1500px] px-4 py-5">{children}</main>
    </>
  );
}
