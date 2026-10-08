"use client";

import { type FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ApplicationDetail, CvVersion } from "../lib/types";

/** Which CV went out with this application. Only a label is kept; the CV file and its text never come here. */
export function CvVersionPicker({ application, onChange }: { application: ApplicationDetail; onChange: (next: ApplicationDetail) => void }) {
  const [versions, setVersions] = useState<CvVersion[]>([]);
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    api.cvVersions()
      .then((result) => { if (current) setVersions(result.cv_versions); })
      .catch((problem: Error) => { if (current) setError(problem.message); });
    return () => { current = false; };
  }, []);

  async function pick(value: string) {
    setError("");
    try {
      onChange((await api.update(application.id, { cv_version_id: value || null })).application);
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!label.trim()) return;
    setError("");
    try {
      const added = (await api.addCvVersion(label.trim())).cv_version;
      setVersions((current) => (current.some((version) => version.id === added.id) ? current : [...current, added]));
      setLabel("");
    } catch (problem) {
      setError((problem as Error).message);
    }
  }

  return (
    <section aria-label="CV sent with this application" className="space-y-2 text-sm">
      <label className="block">
        <span className="mb-1 block font-semibold">CV version</span>
        <select value={application.cv_version_id ?? ""} onChange={(event) => void pick(event.target.value)}
                className="w-full rounded border border-line bg-surface px-2 py-1.5">
          <option value="">Not recorded</option>
          {versions.map((version) => <option key={version.id} value={version.id}>{version.label}</option>)}
        </select>
      </label>
      <form onSubmit={add} className="flex gap-2">
        <input aria-label="New version label" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="e.g. v3-ml"
               className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1.5" />
        <button type="submit" className="rounded border border-line px-2.5 py-1.5 hover:bg-accent-soft">Add version</button>
      </form>
      <p className="text-xs text-muted">Only the label is stored. The CV file stays on your computer.</p>
      {error && <p role="alert" className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-warn">{error}</p>}
    </section>
  );
}
