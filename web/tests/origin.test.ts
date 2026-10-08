import { afterEach, describe, expect, it, vi } from "vitest";

async function redirects(origin?: string) {
  vi.resetModules();
  if (origin === undefined) vi.stubEnv("WEB_ORIGIN", undefined as unknown as string);
  else vi.stubEnv("WEB_ORIGIN", origin);
  const config = (await import("../next.config")).default;
  return (await config.redirects?.()) ?? [];
}

describe("next.config: one exact origin", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("sends any other host name for this app to the origin the API accepts changes from", async () => {
    expect(await redirects()).toEqual([
      { source: "/:path*", missing: [{ type: "host", value: "localhost" }], destination: "http://localhost:3010/:path*", permanent: false },
    ]);
  });

  it("follows WEB_ORIGIN", async () => {
    const [rule] = await redirects("http://localhost:3011/");
    expect(rule.missing).toEqual([{ type: "host", value: "localhost" }]);
    expect(rule.destination).toBe("http://localhost:3011/:path*");
  });
});
