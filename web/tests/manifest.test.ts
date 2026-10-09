import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import manifest from "../app/manifest";

// A PNG's width and height are the two big-endian integers after the IHDR chunk header.
function pngSize(file: string): [number, number] {
  const bytes = readFileSync(path.join(__dirname, "..", "public", file));
  expect(bytes.subarray(1, 4).toString("latin1")).toBe("PNG");
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
}

describe("web app manifest", () => {
  const made = manifest();

  it("meets the install criteria of Chrome and Edge", () => {
    expect(made.name).toBe("Career Agent");
    expect(made.display).toBe("standalone");
    expect(made.prefer_related_applications ?? false).toBe(false);
    const sizes = (made.icons ?? []).map((icon) => icon.sizes);
    expect(sizes).toEqual(expect.arrayContaining(["192x192", "512x512"]));
  });

  it("opens the tracker board on localhost, the origin the API accepts changes from, never 127.0.0.1", () => {
    expect(made.start_url).toBe("http://localhost:3010/app");
    expect(made.scope).toBe("http://localhost:3010/");
  });

  it("names icon files that exist at the stated size", () => {
    for (const icon of made.icons ?? []) {
      const side = Number(String(icon.sizes).split("x")[0]);
      expect(pngSize(icon.src.replace(/^\//, ""))).toEqual([side, side]);
      expect(icon.type).toBe("image/png");
    }
  });
});
