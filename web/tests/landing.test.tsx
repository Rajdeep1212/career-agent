import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Landing } from "../components/landing/Landing";
import { REPOSITORY, RESULTS, sourceUrl } from "../lib/results";

describe("Landing", () => {
  it("has one h1 with the tagline and a heading for every section", () => {
    render(<Landing />);
    expect(screen.getAllByRole("heading", { level: 1 }).map((heading) => heading.textContent)).toEqual(["Evidence-first job search for Indian freshers"]);
    const sections = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(sections).toEqual(["The problem", "How it works", "Measured results", "Privacy and ethics"]);
  });

  it("shows every measured result with its level and a link to its source file", () => {
    render(<Landing />);
    for (const result of RESULTS) {
      const card = within(screen.getByTestId(`result-${result.id}`));
      expect(card.getByText(result.text)).toBeTruthy();
      expect(card.getByText("L0")).toBeTruthy();
      expect(card.getByRole("link", { name: result.source }).getAttribute("href")).toBe(sourceUrl(result));
    }
  });

  it("says the demo is coming soon when no demo address is set, and offers no dead link", () => {
    render(<Landing />);
    expect(screen.getByText("Try the demo: coming soon")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Try the demo/ })).toBeNull();
  });

  it("links to the demo when its address is set", () => {
    render(<Landing demoUrl="https://career-agent.example/app" />);
    expect(screen.getByRole("link", { name: "Try the demo" }).getAttribute("href")).toBe("https://career-agent.example/app");
    expect(screen.queryByText(/coming soon/)).toBeNull();
  });

  it("describes the diagram for people who cannot see it", () => {
    render(<Landing />);
    const diagram = screen.getByRole("img", { name: /How Career Agent works/ });
    expect(diagram.querySelector("desc")?.textContent).toMatch(/official company job boards/);
  });

  it("states the privacy rules and links to the code", () => {
    render(<Landing />);
    for (const rule of ["Local-first", "No scraping of LinkedIn, Naukri or Indeed", "You approve every email", "Your CV never goes to a hosted model"]) {
      expect(screen.getByRole("heading", { level: 3, name: rule })).toBeTruthy();
    }
    expect(screen.getByRole("link", { name: "View the code on GitHub" }).getAttribute("href")).toBe(REPOSITORY);
  });

  it("loads nothing from another site: no script, image, font or stylesheet address", () => {
    const { container } = render(<Landing />);
    expect(container.querySelectorAll("script, img, link, iframe, video, audio").length).toBe(0);
    expect(container.innerHTML).not.toMatch(/fonts\.googleapis|cdn\.|googletagmanager|analytics/i);
  });
});
