import { describe, expect, it } from "vitest";
import { BODY_TEXT_MIN, contrastRatio, LARGE_TEXT_MIN, relativeLuminance } from "./contrast";
import { TEXT_PAIRS, TOKENS } from "./tokens";

describe("contrast ratio", () => {
  it("agrees with the values WCAG names", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    // Order does not matter, and three-digit hex is the same colour as six.
    expect(contrastRatio("#ffffff", "#000000")).toBeCloseTo(21, 5);
    expect(contrastRatio("#fff", "#000")).toBeCloseTo(21, 5);
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 5);
    expect(relativeLuminance("#000000")).toBeCloseTo(0, 5);
  });

  it("refuses something that is not a colour", () => {
    expect(() => contrastRatio("teal", "#ffffff")).toThrow(/hex/);
  });
});

/**
 * The audit the spec asks for, as a test: every token pair the UI puts text on clears the
 * WCAG AA floor. A token change that breaks one fails the build with the pair named.
 */
describe("token contrast audit", () => {
  it.each(TEXT_PAIRS)("$fg on $bg ($where)", ({ fg, bg, large }) => {
    const ratio = contrastRatio(TOKENS[fg], TOKENS[bg]);
    expect(ratio).toBeGreaterThanOrEqual(large ? LARGE_TEXT_MIN : BODY_TEXT_MIN);
  });

  it("covers every colour token that text is ever set in", () => {
    const foregrounds = new Set(TEXT_PAIRS.map((pair) => pair.fg));
    for (const token of ["ink", "ink-soft", "accent", "warn", "risk", "ok", "surface", "on-ink-soft"] as const) {
      expect(foregrounds.has(token)).toBe(true);
    }
  });
});
