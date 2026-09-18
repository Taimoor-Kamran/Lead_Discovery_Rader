import { beforeEach, describe, expect, it } from "vitest";
import { clearAccessToken, getAccessToken, setAccessToken } from "./session";

describe("session", () => {
  beforeEach(() => clearAccessToken());

  it("starts empty", () => {
    expect(getAccessToken()).toBeNull();
  });

  it("holds the token in memory", () => {
    setAccessToken("a-token");
    expect(getAccessToken()).toBe("a-token");
  });

  it("clears the token", () => {
    setAccessToken("a-token");
    clearAccessToken();
    expect(getAccessToken()).toBeNull();
  });

  it("never touches browser storage", () => {
    setAccessToken("a-token");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
