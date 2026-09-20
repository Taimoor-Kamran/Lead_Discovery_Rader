import { describe, expect, it } from "vitest";
import { AI_LABEL, hostOf, safeHttpUrl } from "./safe";

describe("safe URLs", () => {
  it("accepts only http and https", () => {
    expect(safeHttpUrl("https://example.com/a?b=1")).toBe("https://example.com/a?b=1");
    expect(safeHttpUrl("http://example.com")).toBe("http://example.com/");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(safeHttpUrl("ftp://example.com")).toBeNull();
    expect(safeHttpUrl("example.com")).toBeNull();
    expect(safeHttpUrl("")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
    expect(safeHttpUrl(" HTTPS://Example.com ")).toBe("https://example.com/");
  });

  it("extracts the host", () => {
    expect(hostOf("https://www.example.com/path")).toBe("www.example.com");
    expect(hostOf("javascript:alert(1)")).toBeNull();
  });

  it("has the exact label wording", () => {
    expect(AI_LABEL).toBe("AI-generated — verify before use");
  });
});
