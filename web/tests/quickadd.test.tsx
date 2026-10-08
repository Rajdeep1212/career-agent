import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuickAdd } from "../components/QuickAdd";
import { mutate } from "../lib/api";
import { detail, fakeFetch } from "./helpers";

const NORTHWIND = [
  { job_id: "northwind:1", title: "Graduate Software Engineer", company: "Northwind Labs", location: "Hyderabad, India",
    url: "https://jobs.lever.co/northwind/1", reason: "Same company and the same title; the URL is different." },
  { job_id: "northwind:2", title: "Graduate Software Engineer", company: "Northwind Labs", location: "Chennai, India",
    url: "https://jobs.lever.co/northwind/2", reason: "Same company and the same title; the URL is different." }];

function fill(url: string, title = "", company = "") {
  fireEvent.change(screen.getByLabelText("Job URL"), { target: { value: url } });
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: title } });
  fireEvent.change(screen.getByLabelText("Company"), { target: { value: company } });
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
}

describe("QuickAdd", () => {
  it("matched: shows the reason, stores nothing until Add is pressed, then creates from the index job", async () => {
    const created = detail("SAVED", [], ["APPLIED"], { job_id: "acme:4012345" });
    const calls = fakeFetch(
      { status: 200, body: { created: false, application: null,
                             match: { status: "matched", job_id: "acme:4012345", candidates: [], reason: "The URL is the one of index job acme:4012345." } } },
      { status: 201, body: { created: true, application: created, match: { status: "matched", job_id: "acme:4012345", candidates: [], reason: "x" } } });
    const onAdded = vi.fn();
    render(<QuickAdd onAdded={onAdded} />);
    fill("https://boards.greenhouse.io/acmerobotics/jobs/4012345?gh_src=x");
    expect((await screen.findByTestId("match")).textContent).toContain("The URL is the one of index job acme:4012345.");
    expect(screen.getByTestId("match").getAttribute("data-status")).toBe("matched");
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ url: "https://boards.greenhouse.io/acmerobotics/jobs/4012345?gh_src=x", preview: true });
    fireEvent.click(screen.getByRole("button", { name: "Add to tracker" }));
    await waitFor(() => expect(onAdded).toHaveBeenCalledWith(created));
    expect(calls[1].url).toBe("/api/v1/applications/quick-add");
    expect(calls[1].body).toEqual({ url: "https://boards.greenhouse.io/acmerobotics/jobs/4012345?gh_src=x" });
    expect(calls[1].headers["Idempotency-Key"]).toBeTruthy();
    expect(calls[1].headers["Idempotency-Key"]).not.toBe(calls[0].headers["Idempotency-Key"]);
    expect(screen.getByRole("status").textContent).toContain("Added");
  });

  it("probable: lists the candidates and links one only after an explicit confirm", async () => {
    const calls = fakeFetch(
      { status: 200, body: { created: false, application: null,
                             match: { status: "probable", job_id: null, candidates: NORTHWIND, reason: "No index job has this URL. 2 index job(s) match." } } },
      { status: 201, body: { created: true, application: detail("SAVED", [], [], { job_id: "northwind:2" }),
                             match: { status: "probable", job_id: null, candidates: NORTHWIND, reason: "x" } } });
    const onAdded = vi.fn();
    render(<QuickAdd onAdded={onAdded} />);
    fill("https://northwind.example/jobs/graduate-swe", "Graduate Software Engineer", "Northwind Labs");
    const match = await screen.findByTestId("match");
    expect(match.getAttribute("data-status")).toBe("probable");
    expect(match.textContent).toContain("Hyderabad, India");
    expect(match.textContent).toContain("Chennai, India");
    expect(match.textContent).toContain("Same company and the same title");
    expect(calls).toHaveLength(1);                                    // nothing was saved by the check
    expect(onAdded).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Add to tracker" })).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "This is the job" })[1]);
    await waitFor(() => expect(onAdded).toHaveBeenCalled());
    expect(calls[1].body).toEqual({ url: "https://northwind.example/jobs/graduate-swe", title: "Graduate Software Engineer",
                                    company: "Northwind Labs", confirm_job_id: "northwind:2" });
  });

  it("probable: 'none of these' saves what was entered, without a link", async () => {
    const calls = fakeFetch(
      { status: 200, body: { created: false, application: null, match: { status: "probable", job_id: null, candidates: NORTHWIND, reason: "r" } } },
      { status: 201, body: { created: true, application: detail("SAVED", [], []), match: { status: "probable", job_id: null, candidates: NORTHWIND, reason: "r" } } });
    render(<QuickAdd onAdded={() => {}} />);
    fill("https://northwind.example/jobs/graduate-swe", "Graduate Software Engineer", "Northwind Labs");
    fireEvent.click(await screen.findByRole("button", { name: "None of these: save what I entered" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].body).toEqual({ url: "https://northwind.example/jobs/graduate-swe", title: "Graduate Software Engineer", company: "Northwind Labs" });
  });

  it("none: asks for a title and company, and saves only once they are given", async () => {
    const none = { status: "none", job_id: null, candidates: [], reason: "No index job has this URL, and no title and company were given to compare." };
    const calls = fakeFetch(
      { status: 200, body: { created: false, application: null, match: none } },
      { status: 200, body: { created: false, application: null, match: { ...none, reason: "No index job has this URL, and none at this company has a matching title." } } },
      { status: 201, body: { created: true, application: detail("SAVED", [], []), match: none } });
    render(<QuickAdd onAdded={() => {}} />);
    fill("https://initech.example/jobs/12");
    const match = await screen.findByTestId("match");
    expect(match.getAttribute("data-status")).toBe("none");
    expect(match.textContent).toMatch(/enter the title and the company/i);
    expect(screen.queryByRole("button", { name: "Save what I entered" })).toBeNull();
    fill("https://initech.example/jobs/12", "Backend Engineer", "Initech");
    fireEvent.click(await screen.findByRole("button", { name: "Save what I entered" }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2].body).toEqual({ url: "https://initech.example/jobs/12", title: "Backend Engineer", company: "Initech" });
  });

  it("an application that is already tracked is reported as such", async () => {
    fakeFetch(
      { status: 200, body: { created: false, application: null, match: { status: "matched", job_id: "acme:1", candidates: [], reason: "r" } } },
      { status: 200, body: { created: false, application: detail("APPLIED", [], []), match: { status: "matched", job_id: "acme:1", candidates: [], reason: "r" } } });
    render(<QuickAdd onAdded={() => {}} />);
    fill("https://boards.greenhouse.io/acme/jobs/1");
    fireEvent.click(await screen.findByRole("button", { name: "Add to tracker" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/already in your tracker/i));
  });

  it("a bad URL shows the server's message", async () => {
    fakeFetch({ status: 422, body: { detail: { message: "url must be an http or https address." } } });
    render(<QuickAdd onAdded={() => {}} />);
    fill("not a url");
    expect((await screen.findByRole("alert")).textContent).toContain("url must be an http or https address.");
  });
});

describe("mutate", () => {
  it("retries a request that never got an answer once, with the same Idempotency-Key", async () => {
    const calls = fakeFetch(new TypeError("fetch failed"), { status: 201, body: { ok: true } });
    await expect(mutate("POST", "/applications/a1/events", { event_type: "applied" })).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(2);
    expect(calls[0].headers["Idempotency-Key"]).toBe(calls[1].headers["Idempotency-Key"]);
  });

  it("does not retry an answer, and carries the status and the allowed list of a 409", async () => {
    const calls = fakeFetch({ status: 409, body: { detail: { message: "No.", allowed: ["OFFER"] } } });
    await expect(mutate("POST", "/applications/a1/events", { event_type: "saved" })).rejects.toMatchObject(
      { status: 409, message: "No.", allowed: ["OFFER"] });
    expect(calls).toHaveLength(1);
  });
});
