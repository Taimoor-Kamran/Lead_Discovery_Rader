import { describe, expect, it } from "vitest";
import { formatPhone, psiLines, score } from "./format";
import { findingLabel, humanize, serviceLabel } from "./labels";

describe("score", () => {
  it("shows a 0–1 value as a 0–100 integer and never invents one", () => {
    expect(score(0.78)).toBe("78");
    expect(score(0.725)).toBe("73");
    expect(score(1)).toBe("100");
    expect(score(0)).toBe("0");
    expect(score(null)).toBe("—");
    expect(score(undefined)).toBe("—");
  });
});

describe("formatPhone", () => {
  it("formats a stored North American E.164 number the way people dial it", () => {
    expect(formatPhone("+15125550102")).toBe("(512) 555-0102");
  });
  it("leaves any other shape exactly as stored, and says when there is none", () => {
    expect(formatPhone("+442071234567")).toBe("+442071234567");
    expect(formatPhone("512-555")).toBe("512-555");
    expect(formatPhone(null)).toBe("no public phone");
  });
});

describe("labels", () => {
  it("names services and findings in plain words", () => {
    expect(serviceLabel("website_design")).toBe("Website redesign");
    expect(serviceLabel("seo_gbp")).toBe("SEO / Google profile");
    expect(serviceLabel("booking_setup")).toBe("Online booking");
    expect(serviceLabel("ads_social")).toBe("Ads & social");
    expect(findingLabel("no_contact_on_homepage")).toBe("No contact info on homepage");
    expect(findingLabel("no_https")).toBe("No HTTPS");
  });
  it("falls back to a readable version of an unknown code rather than hiding it", () => {
    expect(findingLabel("brand_new_check")).toBe("Brand new check");
    expect(humanize(null)).toBe("unknown");
  });
});

describe("psiLines", () => {
  it("labels the PageSpeed numbers with Google's good / needs work / poor bands", () => {
    const lines = psiLines({ performance_score: 55, lcp_ms: 3600, cls: 0.11, tbt_ms: 150, strategy: "mobile" });
    expect(lines.map((l) => [l.label, l.value, l.rating])).toEqual([
      ["Mobile score", "55/100", "needs work"],
      ["Load time (LCP)", "3.6 s", "needs work"],
      ["Layout shift (CLS)", "0.11", "needs work"],
      ["Blocking time (TBT)", "150 ms", "good"],
    ]);
    expect(psiLines({ performance_score: 95, lcp_ms: 1200, cls: 0.3 }).map((l) => l.rating)).toEqual([
      "good",
      "good",
      "poor",
      null,
    ]);
  });
  it("adds accessibility and best practices only when PageSpeed scored them", () => {
    const scored = psiLines({ performance_score: 92, accessibility_score: 58, best_practices_score: 67 });
    expect(scored.slice(4).map((l) => [l.label, l.value, l.rating])).toEqual([
      ["Accessibility", "58/100", "needs work"],
      ["Best practices", "67/100", "needs work"],
    ]);
    const unscored = psiLines({ performance_score: 92, accessibility_score: null });
    expect(unscored.map((l) => l.label)).not.toContain("Accessibility");
    expect(unscored.map((l) => l.label)).not.toContain("Best practices");
    expect(unscored.map((l) => l.value)).not.toContain("0/100");
  });
  it("says unknown for what PSI did not report, and nothing at all without a measurement", () => {
    const [scoreLine] = psiLines({ performance_score: null, lcp_ms: 900 });
    expect(scoreLine.value).toBe("unknown");
    expect(scoreLine.rating).toBeNull();
    expect(psiLines(null)).toEqual([]);
    expect(psiLines({})).toEqual([]);
  });
});
