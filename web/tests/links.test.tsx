import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Landing } from "../components/landing/Landing";

const ROOT = path.join(__dirname, "..", "..");
const readme = readFileSync(path.join(ROOT, "README.md"), "utf-8");

// GitHub's heading anchors: lower case, punctuation dropped, spaces to hyphens.
const anchor = (heading: string) => heading.trim().toLowerCase().replace(/[^\w\- ]/g, "").replace(/ /g, "-");
const prose = readme.replace(/```[\s\S]*?```/g, "");                      // code blocks hold no links or headings
const headings = new Set([...prose.matchAll(/^#{1,6} (.+)$/gm)].map((match) => anchor(match[1])));
// Markdown links and images; web addresses are not checked (the check is offline).
const targets = [...prose.matchAll(/\]\(([^)\s]+)\)/g)].map((match) => match[1]).filter((target) => !/^[a-z]+:/i.test(target));

describe("internal links", () => {
  it("the README has internal links to check", () => {
    expect(targets.length).toBeGreaterThan(10);
  });

  it("every relative link in the README names a file that exists, and every anchor a heading", () => {
    const broken = targets.filter((target) => {
      const [file, hash] = target.split("#");
      if (file) return !existsSync(path.join(ROOT, decodeURIComponent(file)));
      return !headings.has(hash);
    });
    expect(broken).toEqual([]);
  });

  it("every internal link on the landing page names a route of this app or a section of the page", () => {
    const routes = new Map([["/", "(site)"], ["/app", path.join("(tracker)", "app")]]);
    for (const [route, folder] of routes) {
      expect(existsSync(path.join(__dirname, "..", "app", folder, "page.tsx")), route).toBe(true);
    }
    const { container } = render(<Landing />);
    const links = [...container.querySelectorAll("a")].map((link) => link.getAttribute("href") ?? "");
    const internal = links.filter((href) => href.startsWith("/") || href.startsWith("#"));
    expect(internal.length).toBeGreaterThan(4);
    const broken = internal.filter((href) => (href.startsWith("#") ? !container.querySelector(`[id="${href.slice(1)}"]`) : !routes.has(href)));
    expect(broken).toEqual([]);
  });
});
