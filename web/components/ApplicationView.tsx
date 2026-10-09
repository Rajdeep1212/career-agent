"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";
import { EVENT_FOR, LABEL, day } from "../lib/status";
import type { ApplicationDetail, Status } from "../lib/types";
import { CvVersionPicker } from "./CvVersionPicker";
import { Timeline } from "./Timeline";

/** One application: where it stands, what can happen next, its event log, and the job post as it was when saved. */
export function ApplicationView({ id }: { id: string }) {
  const [application, setApplication] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    api.application(id)
      .then((loaded) => { if (current) setApplication(loaded); })
      .catch((problem: Error) => {
        if (current) setError(problem instanceof ApiError && problem.status === 404 ? "There is no application with this id." : problem.message);
      });
    return () => { current = false; };
  }, [id]);

  async function advance(status: Status) {
    setError("");
    try {
      setApplication((await api.addEvent(id, EVENT_FOR[status])).application);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  if (!application) {
    return error ? <p role="alert" className="text-sm text-warn">{error} <a href="/app" className="underline">Back to the board</a></p>
                 : <p className="text-sm text-muted">Loading…</p>;
  }
  const description = typeof application.snapshot?.description === "string" ? application.snapshot.description : "";
  const location = typeof application.snapshot?.location === "string" ? application.snapshot.location : "";

  return (
    <article className="space-y-5">
      <a href="/app" className="text-sm text-muted hover:underline">← Board</a>
      <header className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">{application.title}</h1>
        <p className="text-sm text-muted">
          {application.company_name}{location && ` · ${location}`}{application.source && ` · ${application.source}`}
        </p>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span data-testid="status" className="rounded bg-accent-soft px-2 py-0.5 font-medium text-accent">{LABEL[application.status]}</span>
          {application.applied_at && <span className="text-muted">Applied {day(application.applied_at)}</span>}
          {application.url && <a href={application.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">Job post</a>}
        </p>
      </header>

      {error && <p role="alert" className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-sm text-warn">{error}</p>}

      <section aria-label="Next step" className="space-y-2">
        <h2 className="text-sm font-semibold">Next step</h2>
        {application.allowed_next.length === 0
          ? <p className="text-sm text-muted">Nothing follows {LABEL[application.status]}. Undo the latest event in the timeline to go back.</p>
          : (
            <div className="flex flex-wrap gap-2">
              {application.allowed_next.map((status) => (
                <button key={status} type="button" onClick={() => void advance(status)}
                        className="rounded border border-line bg-surface px-2.5 py-1.5 text-sm hover:bg-accent-soft">
                  {status === application.status ? `Another ${LABEL[status].toLowerCase()} round` : LABEL[status]}
                </button>
              ))}
            </div>
          )}
      </section>

      <div className="grid gap-6 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Timeline application={application} onChange={setApplication} />
        <CvVersionPicker application={application} onChange={setApplication} />
      </div>

      {description && (
        <section aria-label="Job post as saved">
          <h2 className="mb-1 text-sm font-semibold">Job post as saved</h2>
          <p className="mb-2 text-xs text-muted">Captured {day(application.snapshot_captured_at)}. It stays here if the posting is taken down.</p>
          <p className="max-w-[75ch] whitespace-pre-wrap text-sm leading-relaxed">{description}</p>
        </section>
      )}
    </article>
  );
}
