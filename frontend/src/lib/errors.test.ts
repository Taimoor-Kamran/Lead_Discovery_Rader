import { afterEach, describe, expect, it, vi } from "vitest";
import { apiUnreachable, loadFailed, psCommand } from "./errors";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the API-unreachable message", () => {
  it("names the development stack in a development build", () => {
    for (const environment of ["local", "development", "ci", ""]) {
      vi.stubEnv("NEXT_PUBLIC_ENVIRONMENT", environment);
      expect(psCommand()).toBe("make ps");
    }
    expect(apiUnreachable()).toBe(
      "Couldn't reach the API. Check that the api container is running (`make ps`).",
    );
  });

  it("names the production stack in a production or staging build", () => {
    for (const environment of ["production", "staging", "Production"]) {
      vi.stubEnv("NEXT_PUBLIC_ENVIRONMENT", environment);
      expect(psCommand()).toBe("make prod-ps");
    }
    expect(apiUnreachable()).toBe(
      "Couldn't reach the API. Check that the api container is running (`make prod-ps`).",
    );
  });

  it("falls back to the development stack when the build said nothing", () => {
    vi.stubEnv("NEXT_PUBLIC_ENVIRONMENT", undefined);
    expect(psCommand()).toBe("make ps");
  });

  it("does not follow NODE_ENV, which reads production in the development stack too", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_ENVIRONMENT", "local");
    expect(psCommand()).toBe("make ps");
  });

  it("states the cause and the fix when a page's own request failed", () => {
    vi.stubEnv("NEXT_PUBLIC_ENVIRONMENT", "production");
    expect(loadFailed("the review queue")).toBe(
      "Couldn't load the review queue. Couldn't reach the API. " +
        "Check that the api container is running (`make prod-ps`).",
    );
  });
});
