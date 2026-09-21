import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BusinessFacts } from "./BusinessFacts";
import { BUSINESS_STATUS_LABELS, WEBSITE_KIND_LABELS, industryLabel } from "@/lib/labels";
import { reviewDetail } from "@/test/utils";
import type { ReviewDetail } from "@/lib/api";

function facts(business: Partial<ReviewDetail["business"]> = {}) {
  const base = reviewDetail();
  return { ...base, business: { ...base.business, ...business } };
}

describe("BusinessFacts", () => {
  it("reads the codes in plain words and keeps the raw code in a tooltip", () => {
    render(<BusinessFacts detail={facts()} />);
    const list = screen.getByTestId("business-facts");
    expect(list.textContent).toContain("Plumbing");
    expect(list.textContent).toContain("Own website");
    expect(list.textContent).toContain("Open");
    expect(screen.getByTitle("plumbing").textContent).toBe("Plumbing");
    expect(screen.getByTitle("own_site").textContent).toBe("Own website");
    expect(screen.getByTitle("operational").textContent).toBe("Open");
  });

  it("renders no raw enum value, for any website kind or status the API can send", () => {
    for (const kind of Object.keys(WEBSITE_KIND_LABELS)) {
      for (const status of Object.keys(BUSINESS_STATUS_LABELS)) {
        const { unmount } = render(
          <BusinessFacts
            detail={facts({ website_kind: kind as never, business_status: status as never })}
          />,
        );
        const text = screen.getByTestId("business-facts").textContent ?? "";
        expect(text).not.toContain(kind);
        expect(text).not.toContain(status);
        expect(text).toContain(WEBSITE_KIND_LABELS[kind]);
        expect(text).toContain(BUSINESS_STATUS_LABELS[status]);
        unmount();
      }
    }
  });

  it("reads an industry slug as words, including one the label table does not know", () => {
    for (const slug of ["plumbing", "hvac", "pest_control", "general_contracting"]) {
      // A neutral domain, so the slug can only match the industry row itself.
      const { unmount } = render(
        <BusinessFacts detail={facts({ industry: slug, website: "https://example.invalid/" })} />,
      );
      const text = screen.getByTestId("business-facts").textContent ?? "";
      expect(text).not.toContain(slug);
      expect(text).toContain(industryLabel(slug));
      unmount();
    }
  });

  it("says unknown, and offers no tooltip, when a code is missing", () => {
    render(<BusinessFacts detail={facts({ industry: null as never })} />);
    expect(screen.getByTestId("business-facts").textContent).toContain("unknown");
    expect(screen.queryByTitle("plumbing")).toBeNull();
  });
});
