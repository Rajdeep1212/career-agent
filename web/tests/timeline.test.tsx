import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CvVersionPicker } from "../components/CvVersionPicker";
import { Timeline } from "../components/Timeline";
import { detail, event, fakeFetch } from "./helpers";

const LOG = [event(1, "saved"), event(2, "applied"), event(3, "online_test", { undone: true }), event(4, "undone", { undoes_event_id: 3 }),
             event(5, "interview", { note: "round 1 with the team lead" })];

describe("Timeline", () => {
  it("lists every event in order, marks undone ones, and offers undo only on the latest event that still counts", () => {
    render(<Timeline application={detail("INTERVIEW", LOG, ["OFFER"])} onChange={() => {}} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("data-event"))).toEqual(["saved", "applied", "online_test", "undone", "interview"]);
    expect(items[2].textContent).toMatch(/undone/i);
    expect(items[4].textContent).toContain("round 1 with the team lead");
    const buttons = screen.getAllByRole("button", { name: /undo/i });
    expect(buttons).toHaveLength(1);
    expect(items[4].contains(buttons[0])).toBe(true);
  });

  it("the first event of an application has no undo", () => {
    render(<Timeline application={detail("SAVED", [event(1, "saved")], ["APPLIED"])} onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: /undo/i })).toBeNull();
    render(<Timeline application={detail("SAVED", [event(1, "saved"), event(2, "applied", { undone: true }),
                                                    event(3, "undone", { undoes_event_id: 2 })], ["APPLIED"])} onChange={() => {}} />);
    expect(screen.queryByRole("button", { name: /undo/i })).toBeNull();
  });

  it("undo posts to the event with an Idempotency-Key and hands back the new state", async () => {
    const after = detail("APPLIED", [...LOG.slice(0, 4), event(5, "interview", { undone: true }), event(6, "undone", { undoes_event_id: 5 })], ["INTERVIEW"]);
    const calls = fakeFetch({ status: 201, body: { replayed: false, event: after.timeline[5], application: after } });
    const onChange = vi.fn();
    render(<Timeline application={detail("INTERVIEW", LOG, ["OFFER"])} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(after));
    expect(calls[0].url).toBe("/api/v1/applications/a1/events/5/undo");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("a refused undo shows the reason and changes nothing", async () => {
    fakeFetch({ status: 409, body: { detail: { message: "Only the latest event can be undone.", latest_event_id: 9 } } });
    const onChange = vi.fn();
    render(<Timeline application={detail("INTERVIEW", LOG, ["OFFER"])} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /undo/i }));
    expect((await screen.findByRole("alert")).textContent).toContain("Only the latest event can be undone.");
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("CvVersionPicker", () => {
  const versions = [{ id: "v1", label: "v3-ml", created_at: "2026-09-01", file_sha256: null, notes: "" },
                    { id: "v2", label: "v4-backend", created_at: "2026-09-20", file_sha256: null, notes: "" }];

  it("lists the registered versions and saves the one that is picked", async () => {
    const picked = detail("APPLIED", [], [], { cv_version_id: "v2" });
    const calls = fakeFetch({ status: 200, body: { cv_versions: versions } }, { status: 200, body: { application: picked } });
    const onChange = vi.fn();
    render(<CvVersionPicker application={detail("APPLIED", [], [])} onChange={onChange} />);
    const select = (await screen.findByLabelText("CV version")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(select.options).map((option) => option.text)).toEqual(["Not recorded", "v3-ml", "v4-backend"]));
    fireEvent.change(select, { target: { value: "v2" } });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(picked));
    expect(calls[1].url).toBe("/api/v1/applications/a1");
    expect(calls[1].method).toBe("PATCH");
    expect(calls[1].body).toEqual({ cv_version_id: "v2" });
    expect(calls[1].headers["Idempotency-Key"]).toBeTruthy();
  });

  it("clearing the choice sends null, and a new label can be registered", async () => {
    const calls = fakeFetch({ status: 200, body: { cv_versions: versions } },
                            { status: 200, body: { application: detail("APPLIED", [], []) } },
                            { status: 201, body: { created: true, cv_version: { id: "v3", label: "v5-data", created_at: "2026-10-08", file_sha256: null, notes: "" } } });
    render(<CvVersionPicker application={detail("APPLIED", [], [], { cv_version_id: "v1" })} onChange={() => {}} />);
    const select = (await screen.findByLabelText("CV version")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("v1"));
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].body).toEqual({ cv_version_id: null });
    fireEvent.change(screen.getByLabelText("New version label"), { target: { value: "v5-data" } });
    fireEvent.click(screen.getByRole("button", { name: "Add version" }));
    await waitFor(() => expect(Array.from(select.options).map((option) => option.text)).toContain("v5-data"));
    expect(calls[2].url).toBe("/api/v1/cv-versions");
    expect(calls[2].body).toEqual({ label: "v5-data" });
  });
});
