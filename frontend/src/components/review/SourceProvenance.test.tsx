/**
 * "Where this came from" is the sentence a reviewer reads out when a prospect asks where
 * their details came from, so the tests here are about the *wording and the attribution*,
 * not only about the values being present.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SourceCell, SourceProvenance } from "@/components/review/SourceProvenance";
import { sourceRecord } from "@/test/utils";

const NO_PROFILES = { page_url: null, profiles: [] };

describe("the source block", () => {
  it("names the source in human words and keeps the raw code in the tooltip", () => {
    render(<SourceProvenance sources={[sourceRecord()]} linkedProfiles={NO_PROFILES} />);

    const name = screen.getByTestId("source-name");
    expect(name.textContent).toBe("Google Places");
    expect(name.getAttribute("title")).toBe("google_places");
  });

  it("links to the source record, in a new tab and without leaking the referrer", () => {
    render(<SourceProvenance sources={[sourceRecord()]} linkedProfiles={NO_PROFILES} />);

    const link = screen.getByRole("link", { name: "ChIJbarton1" });
    expect(link.getAttribute("href")).toBe("https://maps.invalid/ChIJbarton1");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("shows a record with no URL as text rather than as a broken link", () => {
    render(
      <SourceProvenance
        sources={[sourceRecord({ source_url: null })]}
        linkedProfiles={NO_PROFILES}
      />,
    );

    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("ChIJbarton1")).toBeTruthy();
  });

  it("refuses a source_url that is not http(s)", () => {
    render(
      <SourceProvenance
        sources={[sourceRecord({ source_url: "javascript:alert(1)" })]}
        linkedProfiles={NO_PROFILES}
      />,
    );

    expect(screen.queryByRole("link")).toBeNull();
  });

  it("gives both when it was first found and when it was last seen again", () => {
    render(<SourceProvenance sources={[sourceRecord()]} linkedProfiles={NO_PROFILES} />);

    expect(screen.getByTestId("source-discovered").textContent).not.toBe(
      screen.getByTestId("source-last-seen").textContent,
    );
    expect(screen.getByText("First found")).toBeTruthy();
    expect(screen.getByText("Last seen")).toBeTruthy();
  });

  it("lists one entry per contributing record, not one per source", () => {
    render(
      <SourceProvenance
        sources={[
          sourceRecord({ source_record_id: "ChIJone" }),
          sourceRecord({ source_record_id: "ChIJtwo" }),
        ]}
        linkedProfiles={NO_PROFILES}
      />,
    );

    expect(screen.getAllByTestId("source-entry")).toHaveLength(2);
  });

  it("says so plainly when nothing is on file, rather than showing an empty card", () => {
    render(<SourceProvenance sources={[]} linkedProfiles={NO_PROFILES} />);

    expect(screen.queryByTestId("source-list")).toBeNull();
    expect(screen.getByText(/No source record is on file/)).toBeTruthy();
  });
});

describe("profiles linked from the business's own website", () => {
  const LINKED = {
    page_url: "https://bartoncreekplumbing.invalid/",
    profiles: [
      { platform: "facebook", url: "https://www.facebook.invalid/barton" },
      { platform: "nextdoor", url: null },
    ],
  };

  it("attributes them to the business's own homepage, under its own heading", () => {
    render(<SourceProvenance sources={[sourceRecord()]} linkedProfiles={LINKED} />);

    const block = screen.getByTestId("linked-profiles");
    expect(screen.getByRole("heading", { name: "Linked from their website" })).toBeTruthy();
    expect(block.textContent).toContain("their own homepage links to");
    // The promise a prospect will test: we recorded the link, we did not read the profile.
    expect(block.textContent).toContain("None of these profiles was opened or read");
  });

  it("names each platform in words, with the code in the tooltip", () => {
    render(<SourceProvenance sources={[sourceRecord()]} linkedProfiles={LINKED} />);

    const items = screen.getAllByTestId("linked-profile");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("Facebook");
    expect(screen.getByTitle("nextdoor").textContent).toBe("Nextdoor");
  });

  it("names a platform whose link was not recorded, without inventing one", () => {
    render(<SourceProvenance sources={[sourceRecord()]} linkedProfiles={LINKED} />);

    const nextdoor = screen.getAllByTestId("linked-profile")[1];
    expect(nextdoor.textContent).toContain("Nextdoor");
    expect(nextdoor.querySelector("a")).toBeNull();
  });

  it("is absent entirely when the homepage links nowhere", () => {
    render(<SourceProvenance sources={[sourceRecord()]} linkedProfiles={NO_PROFILES} />);

    expect(screen.queryByTestId("linked-profiles")).toBeNull();
  });

  it("survives a response that omits the block", () => {
    render(
      <SourceProvenance
        sources={[sourceRecord()]}
        linkedProfiles={undefined as unknown as typeof NO_PROFILES}
      />,
    );

    expect(screen.queryByTestId("linked-profiles")).toBeNull();
    expect(screen.getByTestId("source-list")).toBeTruthy();
  });
});

describe("the compact source column", () => {
  it("names each source in words with the code in the tooltip", () => {
    render(<SourceCell codes={["google_places", "demo_fixture"]} />);

    const items = screen.getAllByTestId("source-cell-item");
    expect(items.map((item) => item.textContent)).toEqual(["Google Places", "Demo fixture"]);
    expect(items[0].getAttribute("title")).toBe("google_places");
  });

  it("says a business has no record rather than leaving the cell blank", () => {
    render(<SourceCell codes={[]} />);

    expect(screen.getByText("no record")).toBeTruthy();
  });
});
