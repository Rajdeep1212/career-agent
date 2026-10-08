import { vi } from "vitest";
import type { ApplicationDetail, ApplicationSummary, Status, TimelineEvent } from "../lib/types";

export function summary(id: string, title: string, status: Status, extra: Partial<ApplicationSummary> = {}): ApplicationSummary {
  return { id, title, company_name: "ExampleCo", company_id: null, job_id: null, url: `https://jobs.example.com/${id}`, source: null,
           channel: null, status, applied_at: null, cv_version_id: null, next_follow_up_at: null, notes: "",
           created_at: "2026-10-01T09:00:00+00:00", updated_at: "2026-10-01T09:00:00+00:00", ...extra };
}

export function event(id: number, event_type: string, extra: Partial<TimelineEvent> = {}): TimelineEvent {
  return { id, event_type, occurred_at: `2026-10-0${id}T09:00:00+00:00`, occurred_at_exact: true, recorded_at: `2026-10-0${id}T09:00:00+00:00`,
           source: "user", request_id: `k${id}`, undoes_event_id: null, note: "", undone: false, ...extra };
}

export function detail(status: Status, timeline: TimelineEvent[], allowed_next: Status[], extra: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return { ...summary("a1", "ML Engineer", status), allowed_next, snapshot: { description: "Freshers welcome." }, snapshot_sha256: "ab",
           snapshot_captured_at: "2026-10-01T09:00:00+00:00", timeline, ...extra };
}

type Reply = { status: number; body: unknown } | Error;

/** Replaces fetch with a queue of replies and records every call. */
export function fakeFetch(...replies: Reply[]) {
  const calls: { url: string; method: string; headers: Record<string, string>; body: any }[] = [];
  const queue = [...replies];
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method ?? "GET", headers: (init.headers ?? {}) as Record<string, string>,
                 body: init.body ? JSON.parse(String(init.body)) : undefined });
    const reply = queue.shift();
    if (!reply) throw new Error(`unexpected request: ${init.method ?? "GET"} ${url}`);
    if (reply instanceof Error) throw reply;
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}
