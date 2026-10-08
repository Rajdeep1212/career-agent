"use client";

import { useRef, useState } from "react";
import { ApiError, api } from "../lib/api";
import { COLUMNS, EVENT_FOR, LABEL, day } from "../lib/status";
import type { ApplicationSummary, Status } from "../lib/types";

interface Refusal {
  title: string;
  message: string;
}

function refusalText(error: unknown, from: Status): string {
  if (error instanceof ApiError && error.status === 409 && error.allowed) {
    return error.allowed.length
      ? `${LABEL[from]} can only move to: ${error.allowed.map((status) => LABEL[status as Status] ?? status).join(", ")}.`
      : `Nothing follows ${LABEL[from]}. Open the application and undo its latest event to go back.`;
  }
  return (error as Error).message || "The move was not saved.";
}

/** One column per status. Dropping a card on a column asks the server for that transition; the server decides. */
export function Kanban({ initial }: { initial: ApplicationSummary[] }) {
  const [applications, setApplications] = useState(initial);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [over, setOver] = useState<Status | null>(null);
  const dragged = useRef<string | null>(null);

  async function move(id: string, target: Status) {
    const application = applications.find((item) => item.id === id);
    if (!application || application.status === target) return;
    const from = application.status;
    const place = (status: Status) =>
      setApplications((current) => current.map((item) => (item.id === id ? { ...item, status } : item)));
    setRefusal(null);
    place(target);                                   // shown at once; put back if the server refuses
    try {
      const result = await api.addEvent(id, EVENT_FOR[target]);
      setApplications((current) => current.map((item) => (item.id === id ? { ...item, ...summaryOf(result.application) } : item)));
    } catch (error) {
      place(from);
      setRefusal({ title: application.title, message: refusalText(error, from) });
    }
  }

  return (
    <section aria-label="Applications by status" className="space-y-3">
      {refusal && (
        <p role="alert" className="flex items-start justify-between gap-3 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-sm text-warn">
          <span><strong className="font-semibold">{refusal.title}</strong> was not moved. {refusal.message}</span>
          <button type="button" onClick={() => setRefusal(null)} className="shrink-0 underline underline-offset-2">Dismiss</button>
        </p>
      )}
      <div className="flex gap-3 overflow-x-auto pb-3">
        {COLUMNS.map((status) => {
          const cards = applications.filter((application) => application.status === status);
          return (
            <div
              key={status}
              data-testid={`column-${status}`}
              onDragOver={(event) => { event.preventDefault(); setOver(status); }}
              onDragLeave={() => setOver((current) => (current === status ? null : current))}
              onDrop={(event) => {
                event.preventDefault();
                setOver(null);
                const id = dragged.current ?? event.dataTransfer?.getData("text/plain");
                dragged.current = null;
                if (id) void move(id, status);
              }}
              className={`flex w-60 shrink-0 flex-col rounded-lg border px-2 pb-2 pt-2.5 ${over === status ? "border-accent bg-accent-soft" : "border-line bg-surface"}`}
            >
              <h2 className="mb-2 flex items-baseline justify-between px-1 text-sm font-semibold">
                {LABEL[status]}
                <span data-testid={`count-${status}`} className="text-xs font-normal tabular-nums text-muted">{cards.length}</span>
              </h2>
              <ul className="flex min-h-16 flex-col gap-2">
                {cards.map((application) => (
                  <li
                    key={application.id}
                    data-testid={`card-${application.id}`}
                    draggable
                    onDragStart={(event) => {
                      dragged.current = application.id;
                      event.dataTransfer?.setData("text/plain", application.id);
                    }}
                    className="cursor-grab rounded-md border border-line bg-paper p-2.5 text-sm active:cursor-grabbing"
                  >
                    <a href={`/applications/${encodeURIComponent(application.id)}`} className="block font-medium leading-snug hover:underline">
                      {application.title}
                    </a>
                    <p className="mt-0.5 text-muted">{application.company_name}</p>
                    {application.applied_at && <p className="mt-1 text-xs text-muted">Applied {day(application.applied_at)}</p>}
                    <div className="mt-2">
                      <select
                        aria-label={`Move ${application.title} to`}
                        value={application.status}
                        onChange={(event) => void move(application.id, event.target.value as Status)}
                        className="w-full rounded border border-line bg-surface px-1.5 py-1 text-xs text-ink"
                      >
                        {COLUMNS.map((option) => <option key={option} value={option}>{LABEL[option]}</option>)}
                      </select>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function summaryOf(application: ApplicationSummary): Partial<ApplicationSummary> {
  return { status: application.status, applied_at: application.applied_at, updated_at: application.updated_at };
}
