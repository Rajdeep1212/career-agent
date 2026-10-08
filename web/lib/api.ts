import type { ApplicationDetail, ApplicationSummary, CvVersion, QuickAddResult, TimelineEvent } from "./types";

// Every request goes to this app's own /api/v1, which next.config.ts passes on to the local FastAPI server.
const BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  allowed?: string[];
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
    const allowed = (detail as { allowed?: unknown } | null)?.allowed;
    if (Array.isArray(allowed)) this.allowed = allowed as string[];
  }
}

export function newKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function messageOf(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item?.msg ?? String(item)).join("; ");
  const message = (detail as { message?: unknown } | null)?.message;
  return typeof message === "string" ? message : `The server answered ${status}.`;
}

async function read<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, messageOf(response.status, body?.detail), body?.detail ?? null);
  return body as T;
}

export async function get<T>(path: string): Promise<T> {
  return read<T>(await fetch(BASE + path, { headers: { Accept: "application/json" } }));
}

/** A change. It carries an Idempotency-Key, so the one retry after a lost connection cannot record anything twice. */
export async function mutate<T>(method: "POST" | "PATCH", path: string, body: unknown = {}): Promise<T> {
  const init = { method, headers: { "Content-Type": "application/json", "Idempotency-Key": newKey() }, body: JSON.stringify(body) };
  let response: Response;
  try {
    response = await fetch(BASE + path, init);
  } catch {
    response = await fetch(BASE + path, init);
  }
  return read<T>(response);
}

export interface EventResult {
  replayed: boolean;
  event: TimelineEvent;
  application: ApplicationDetail;
}

export const api = {
  applications: () => get<{ count: number; applications: ApplicationSummary[] }>("/applications"),
  application: (id: string) => get<ApplicationDetail>(`/applications/${encodeURIComponent(id)}`),
  addEvent: (id: string, eventType: string) =>
    mutate<EventResult>("POST", `/applications/${encodeURIComponent(id)}/events`, { event_type: eventType }),
  undo: (id: string, eventId: number) => mutate<EventResult>("POST", `/applications/${encodeURIComponent(id)}/events/${eventId}/undo`),
  quickAdd: (body: Record<string, unknown>) => mutate<QuickAddResult>("POST", "/applications/quick-add", body),
  update: (id: string, changes: Record<string, unknown>) =>
    mutate<{ application: ApplicationDetail }>("PATCH", `/applications/${encodeURIComponent(id)}`, changes),
  cvVersions: () => get<{ cv_versions: CvVersion[] }>("/cv-versions"),
  addCvVersion: (label: string) => mutate<{ created: boolean; cv_version: CvVersion }>("POST", "/cv-versions", { label }),
};
