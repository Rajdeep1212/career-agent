"use client";

import { useCallback, useEffect, useState } from "react";
import { Kanban } from "../../../components/Kanban";
import { QuickAdd } from "../../../components/QuickAdd";
import { api } from "../../../lib/api";
import type { ApplicationSummary } from "../../../lib/types";

export default function BoardPage() {
  const [applications, setApplications] = useState<ApplicationSummary[] | null>(null);
  const [version, setVersion] = useState(0);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setApplications((await api.applications()).applications);
      setVersion((current) => current + 1);
      setError("");
    } catch (problem) {
      setError(`The tracker could not be loaded: ${(problem as Error).message} Is the FastAPI server running on port 8010?`);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-5">
      <QuickAdd onAdded={() => void load()} />
      {error && <p role="alert" className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-sm text-warn">{error}</p>}
      {applications === null && !error && <p className="text-sm text-muted">Loading applications…</p>}
      {applications !== null && <Kanban key={version} initial={applications} />}
    </div>
  );
}
