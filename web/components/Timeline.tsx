"use client";

import { useState } from "react";
import { api } from "../lib/api";
import { EVENT_LABEL, day } from "../lib/status";
import type { ApplicationDetail } from "../lib/types";

/** The event log, oldest first. Only the latest event that still counts can be undone, and never the first one. */
export function Timeline({ application, onChange }: { application: ApplicationDetail; onChange: (next: ApplicationDetail) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const events = application.timeline;
  const live = events.filter((event) => event.event_type !== "undone" && !event.undone);
  const latest = live.at(-1);
  const undoable = latest && events.length > 0 && latest.id !== events[0].id ? latest.id : null;

  async function undo(eventId: number) {
    setBusy(true);
    setError("");
    try {
      onChange((await api.undo(application.id, eventId)).application);
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Timeline">
      <h2 className="mb-2 text-sm font-semibold">Timeline</h2>
      {error && <p role="alert" className="mb-2 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-sm text-warn">{error}</p>}
      <ol className="border-l border-line">
        {events.map((event) => (
          <li key={event.id} data-event={event.event_type} className="relative ml-4 pb-3 text-sm">
            <span aria-hidden className="absolute -left-[21px] top-1.5 size-2.5 rounded-full border border-line bg-surface" />
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className={event.undone ? "text-muted line-through" : "font-medium"}>
                {event.event_type === "undone" ? `Undid event ${event.undoes_event_id}` : EVENT_LABEL[event.event_type] ?? event.event_type}
              </span>
              {event.undone && <span className="text-xs text-muted">undone</span>}
              <time dateTime={event.occurred_at} className="text-xs tabular-nums text-muted">
                {event.occurred_at_exact ? event.occurred_at.slice(0, 16).replace("T", " ") : day(event.occurred_at)}
              </time>
              {event.source !== "user" && <span className="text-xs text-muted">{event.source}</span>}
              {event.id === undoable && (
                <button type="button" disabled={busy} onClick={() => void undo(event.id)}
                        className="rounded border border-line px-2 py-0.5 text-xs hover:bg-accent-soft disabled:opacity-60">
                  Undo
                </button>
              )}
            </div>
            {event.note && <p className="mt-0.5 text-muted">{event.note}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}
