import type { Status } from "./types";

// The Kanban columns, in order. SKIPPED ("dismissed before applying") is a status but not a column.
export const COLUMNS: Status[] = ["SAVED", "APPLIED", "ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"];

export const LABEL: Record<Status, string> = {
  SAVED: "Saved", APPLIED: "Applied", ONLINE_TEST: "Online test", INTERVIEW: "Interview", OFFER: "Offer", REJECTED: "Rejected",
  WITHDRAWN: "Withdrawn", NO_RESPONSE: "No response", SKIPPED: "Skipped",
};

// The event that moves an application into a status. The server decides whether the move is allowed.
export const EVENT_FOR: Record<Status, string> = {
  SAVED: "saved", APPLIED: "applied", ONLINE_TEST: "online_test", INTERVIEW: "interview", OFFER: "offer", REJECTED: "rejected",
  WITHDRAWN: "withdrawn", NO_RESPONSE: "no_response_confirmed", SKIPPED: "skipped",
};

export const EVENT_LABEL: Record<string, string> = {
  saved: "Saved", applied: "Applied", online_test: "Online test", interview: "Interview", offer: "Offer", rejected: "Rejected",
  withdrawn: "Withdrawn", skipped: "Skipped", no_response_confirmed: "No response confirmed", recruiter_reply: "Recruiter replied",
  note: "Note", undone: "Undo", outreach_prepared: "Outreach prepared", outreach_sent: "Outreach sent", thumbs_up: "Thumbs up",
  thumbs_down: "Thumbs down", thumbs_cleared: "Thumbs cleared", removed_from_results: "Removed from results",
  follow_up_sent: "Follow-up sent",
};

export function day(value: string | null): string {
  return value ? value.slice(0, 10) : "";
}
