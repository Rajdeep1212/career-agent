import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Kanban } from "../components/Kanban";
import { COLUMNS } from "../lib/status";
import { detail, event, fakeFetch, summary } from "./helpers";

const APPS = [summary("a1", "ML Engineer", "SAVED"), summary("a2", "Data Engineer", "APPLIED"), summary("a3", "Old role", "SKIPPED")];

function drag(cardId: string, column: string) {
  const dataTransfer = { setData: () => {}, getData: () => cardId, effectAllowed: "", dropEffect: "" };
  fireEvent.dragStart(screen.getByTestId(`card-${cardId}`), { dataTransfer });
  fireEvent.dragOver(screen.getByTestId(`column-${column}`), { dataTransfer });
  fireEvent.drop(screen.getByTestId(`column-${column}`), { dataTransfer });
}

describe("Kanban", () => {
  it("shows one column per status with its cards and counts, and no column for skipped jobs", () => {
    render(<Kanban initial={APPS} />);
    expect(COLUMNS).toEqual(["SAVED", "APPLIED", "ONLINE_TEST", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "NO_RESPONSE"]);
    for (const status of COLUMNS) expect(screen.getByTestId(`column-${status}`)).toBeTruthy();
    expect(within(screen.getByTestId("column-SAVED")).getByText("ML Engineer")).toBeTruthy();
    expect(within(screen.getByTestId("column-APPLIED")).getByText("Data Engineer")).toBeTruthy();
    expect(screen.getByTestId("count-SAVED").textContent).toBe("1");
    expect(screen.getByTestId("count-OFFER").textContent).toBe("0");
    expect(screen.queryByText("Old role")).toBeNull();
    expect(screen.getByRole("link", { name: /ML Engineer/ }).getAttribute("href")).toBe("/applications/a1");
  });

  it("dragging a card to an allowed column posts the event with an Idempotency-Key and moves the card", async () => {
    const calls = fakeFetch({ status: 201, body: { replayed: false, event: event(2, "applied"),
                                                   application: detail("APPLIED", [event(1, "saved"), event(2, "applied")], ["ONLINE_TEST"]) } });
    render(<Kanban initial={APPS} />);
    drag("a1", "APPLIED");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe("/api/v1/applications/a1/events");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({ event_type: "applied" });
    expect(calls[0].headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(within(screen.getByTestId("column-APPLIED")).getByText("ML Engineer")).toBeTruthy());
    expect(screen.getByTestId("count-APPLIED").textContent).toBe("2");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a refused transition shows the allowed list from the 409 and the card snaps back", async () => {
    const calls = fakeFetch({ status: 409, body: { detail: { message: "APPLIED cannot become SAVED. Allowed next: ONLINE_TEST, INTERVIEW.",
                                                             status: "APPLIED", allowed: ["ONLINE_TEST", "INTERVIEW"], allowed_events: [] } } });
    render(<Kanban initial={APPS} />);
    drag("a2", "SAVED");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Data Engineer");
    expect(alert.textContent).toContain("Online test");
    expect(alert.textContent).toContain("Interview");
    expect(within(screen.getByTestId("column-APPLIED")).getByText("Data Engineer")).toBeTruthy();
    expect(within(screen.getByTestId("column-SAVED")).queryByText("Data Engineer")).toBeNull();
    expect(calls[0].body).toEqual({ event_type: "saved" });
  });

  it("a status that nothing follows says so, and points to undo", async () => {
    fakeFetch({ status: 409, body: { detail: { message: "REJECTED cannot become OFFER.", status: "REJECTED", allowed: [] } } });
    render(<Kanban initial={[summary("a9", "Closed role", "REJECTED")]} />);
    drag("a9", "OFFER");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/nothing follows/i);
    expect(alert.textContent).toMatch(/undo/i);
    expect(within(screen.getByTestId("column-REJECTED")).getByText("Closed role")).toBeTruthy();
  });

  it("dropping a card on its own column sends nothing, and two moves use two keys", async () => {
    const calls = fakeFetch(
      { status: 201, body: { replayed: false, event: event(2, "applied"), application: detail("APPLIED", [], []) } },
      { status: 201, body: { replayed: false, event: event(3, "interview"), application: detail("INTERVIEW", [], []) } });
    render(<Kanban initial={APPS} />);
    drag("a1", "SAVED");
    expect(calls).toHaveLength(0);
    drag("a1", "APPLIED");
    await waitFor(() => expect(calls).toHaveLength(1));
    drag("a1", "INTERVIEW");
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0].headers["Idempotency-Key"]).not.toBe(calls[1].headers["Idempotency-Key"]);
  });

  it("a card can be moved without dragging, from its menu", async () => {
    const calls = fakeFetch({ status: 201, body: { replayed: false, event: event(2, "skipped"), application: detail("SKIPPED", [], []) } });
    render(<Kanban initial={APPS} />);
    fireEvent.change(screen.getByLabelText("Move ML Engineer to"), { target: { value: "APPLIED" } });
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ event_type: "applied" });
  });
});
