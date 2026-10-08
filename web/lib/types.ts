export type Status = "SAVED" | "APPLIED" | "ONLINE_TEST" | "INTERVIEW" | "OFFER" | "REJECTED" | "WITHDRAWN" | "NO_RESPONSE" | "SKIPPED";

export interface ApplicationSummary {
  id: string;
  title: string;
  company_name: string;
  company_id: string | null;
  job_id: string | null;
  url: string | null;
  source: string | null;
  channel: string | null;
  status: Status;
  applied_at: string | null;
  cv_version_id: string | null;
  next_follow_up_at: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface TimelineEvent {
  id: number;
  event_type: string;
  occurred_at: string;
  occurred_at_exact: boolean;
  recorded_at: string;
  source: string;
  request_id: string | null;
  undoes_event_id: number | null;
  note: string;
  undone: boolean;
}

export interface ApplicationDetail extends ApplicationSummary {
  allowed_next: Status[];
  snapshot: Record<string, unknown> | null;
  snapshot_sha256: string | null;
  snapshot_captured_at: string | null;
  timeline: TimelineEvent[];
}

export interface Candidate {
  job_id: string;
  title: string;
  company: string;
  location: string;
  url: string | null;
  reason: string;
}

export interface Match {
  status: "matched" | "probable" | "none";
  job_id: string | null;
  candidates: Candidate[];
  reason: string;
}

export interface QuickAddResult {
  created: boolean;
  match: Match;
  application: ApplicationDetail | null;
}

export interface CvVersion {
  id: string;
  label: string;
  created_at: string;
  file_sha256: string | null;
  notes: string;
}
