"use client";

import { type FormEvent, useState } from "react";
import { api } from "../lib/api";
import type { ApplicationDetail, Match } from "../lib/types";

const HEADING: Record<Match["status"], string> = {
  matched: "This job is in the index",
  probable: "This may be a job in the index",
  none: "Not in the index",
};
const FIELD = "min-w-0 rounded border border-line bg-surface px-2 py-1.5 text-sm";
const BUTTON = "rounded border border-line px-2.5 py-1.5 text-sm hover:bg-accent-soft disabled:opacity-60";

/**
 * Add an application from a job's URL. "Check" asks the server whether the job is in the index and stores nothing;
 * the application is created only by the button pressed after that. A probable match is linked only when chosen.
 */
export function QuickAdd({ onAdded }: { onAdded: (application: ApplicationDetail) => void }) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [match, setMatch] = useState<Match | null>(null);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const given = () => ({ url: url.trim(), ...(title.trim() ? { title: title.trim() } : {}), ...(company.trim() ? { company: company.trim() } : {}) });
  const edit = (set: (value: string) => void) => (value: string) => { set(value); setMatch(null); setDone(""); };

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function check(event: FormEvent) {
    event.preventDefault();
    setDone("");
    setMatch(null);
    void run(async () => setMatch((await api.quickAdd({ ...given(), preview: true })).match));
  }

  function save(body: Record<string, unknown>) {
    void run(async () => {
      const result = await api.quickAdd(body);
      const application = result.application as ApplicationDetail;
      setDone(result.created ? `Added: ${application.title} at ${application.company_name}.`
                             : `Already in your tracker: ${application.title} at ${application.company_name} (${application.status}).`);
      setMatch(null);
      setUrl("");
      setTitle("");
      setCompany("");
      onAdded(application);
    });
  }

  const hasFields = Boolean(title.trim() && company.trim());

  return (
    <section aria-label="Add an application" className="rounded-lg border border-line bg-surface p-3">
      <form onSubmit={check} className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-64 flex-[2] flex-col gap-1 text-xs text-muted">
          Job URL
          <input aria-label="Job URL" required value={url} onChange={(event) => edit(setUrl)(event.target.value)}
                 placeholder="https://…" className={FIELD + " text-ink"} />
        </label>
        <label className="flex min-w-40 flex-1 flex-col gap-1 text-xs text-muted">
          Title
          <input aria-label="Title" value={title} onChange={(event) => edit(setTitle)(event.target.value)} className={FIELD + " text-ink"} />
        </label>
        <label className="flex min-w-40 flex-1 flex-col gap-1 text-xs text-muted">
          Company
          <input aria-label="Company" value={company} onChange={(event) => edit(setCompany)(event.target.value)} className={FIELD + " text-ink"} />
        </label>
        <button type="submit" disabled={busy} className={BUTTON + " bg-accent text-surface hover:bg-accent"}>Check</button>
      </form>
      <p className="mt-1.5 text-xs text-muted">The page is not opened. The URL is compared with the jobs already in the index.</p>

      {error && <p role="alert" className="mt-3 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-sm text-warn">{error}</p>}
      {done && <p role="status" className="mt-3 rounded-md border border-good/30 bg-good-soft px-3 py-2 text-sm text-good">{done}</p>}

      {match && (
        <div data-testid="match" data-status={match.status} className="mt-3 space-y-2 border-t border-line pt-3 text-sm">
          <p><strong className="font-semibold">{HEADING[match.status]}.</strong> {match.reason}</p>

          {match.status === "matched" && (
            <button type="button" disabled={busy} onClick={() => save({ url: url.trim() })} className={BUTTON}>Add to tracker</button>
          )}

          {match.status === "probable" && (
            <>
              <ul className="space-y-2">
                {match.candidates.map((candidate) => (
                  <li key={candidate.job_id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line bg-paper px-3 py-2">
                    <span>
                      <span className="font-medium">{candidate.title}</span>, {candidate.company}
                      <span className="text-muted"> · {candidate.location}</span>
                      <span className="block text-xs text-muted">{candidate.reason}</span>
                    </span>
                    <button type="button" disabled={busy} onClick={() => save({ ...given(), confirm_job_id: candidate.job_id })} className={BUTTON}>
                      This is the job
                    </button>
                  </li>
                ))}
              </ul>
              <button type="button" disabled={busy} onClick={() => save(given())} className={BUTTON}>None of these: save what I entered</button>
            </>
          )}

          {match.status === "none" && (hasFields
            ? <button type="button" disabled={busy} onClick={() => save(given())} className={BUTTON}>Save what I entered</button>
            : <p className="text-muted">Enter the title and the company above, then check again to save it.</p>)}
        </div>
      )}
    </section>
  );
}
