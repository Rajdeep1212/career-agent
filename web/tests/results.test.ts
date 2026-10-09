import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { RESULTS } from "../lib/results";

const ROOT = path.join(__dirname, "..", "..");
const read = (file: string) => readFileSync(path.join(ROOT, file), "utf-8");
const squeeze = (text: string) => text.replace(/\s+/g, "");

// The README's "Evaluation" section: from its heading to the next second-level heading.
const readme = read("README.md");
const start = readme.indexOf("\n## Evaluation");
const evaluation = readme.slice(start, readme.indexOf("\n## ", start + 5));

describe("measured results: one source of truth", () => {
  it("the README has an Evaluation section", () => {
    expect(start).toBeGreaterThan(0);
    expect(evaluation.length).toBeGreaterThan(500);
  });

  for (const result of RESULTS) {
    it(`${result.id}: the page text states every figure it lists`, () => {
      for (const figure of result.figures) expect(result.text, figure).toContain(figure);
    });

    it(`${result.id}: the README's Evaluation table agrees and cites the same file`, () => {
      for (const figure of result.figures) expect(evaluation, `README lacks "${figure}"`).toContain(figure);
      expect(evaluation).toContain(`(${result.source})`);
    });

    it(`${result.id}: the cited file holds the figures`, () => {
      const source = squeeze(read(result.source));
      for (const figure of result.inSource) expect(source, `${result.source} lacks "${figure}"`).toContain(squeeze(figure));
    });
  }
});
