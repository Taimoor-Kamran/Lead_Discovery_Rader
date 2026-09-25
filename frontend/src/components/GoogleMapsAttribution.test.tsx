import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { GOOGLE_MAPS_LOGO, GoogleMapsAttribution, distinctProviders } from "./GoogleMapsAttribution";

/**
 * Google's "Policies and attributions for Places API" (2026-09-24) is the source for every
 * assertion here: "Google Maps", the official logo unmodified, an accessibility label, the
 * name never translated, and third-party `attributions` shown with the result.
 */
describe("GoogleMapsAttribution", () => {
  it("shows the official Google Maps logo, labelled, at 18 px, never translated", () => {
    render(<GoogleMapsAttribution />);
    const logo = screen.getByRole("img", { name: "Google Maps" });
    expect(logo.getAttribute("src")).toContain("GoogleMaps_Logo_Gray.svg");
    expect(logo.getAttribute("height")).toBe("18");
    expect(logo.closest("[translate]")?.getAttribute("translate")).toBe("no");
    expect(screen.queryByTestId("data-providers")).toBeNull();
    // The old wording is gone: the policy now says "Google Maps", not "Powered by Google".
    expect(document.body.textContent).not.toContain("Powered by");
  });

  it("names every third-party data provider, linked where Places gave a link", () => {
    render(
      <GoogleMapsAttribution
        providers={[
          { provider: "Example Data Co", provider_uri: "https://data.invalid/" },
          { provider: "Second Source", provider_uri: null },
          { provider: "Sneaky", provider_uri: "javascript:alert(1)" },
        ]}
      />,
    );
    const providers = screen.getByTestId("data-providers");
    expect(providers.textContent).toBe("Data from Example Data Co, Second Source, Sneaky");
    expect(screen.getByRole("link", { name: "Example Data Co" }).getAttribute("href")).toBe("https://data.invalid/");
    expect(screen.queryByRole("link", { name: "Sneaky" })).toBeNull();
  });

  it("collects each provider once across a page of rows", () => {
    const a = { provider: "A", provider_uri: null };
    const b = { provider: "B", provider_uri: "https://b.invalid/" };
    expect(distinctProviders([{ data_providers: [a] }, { data_providers: [b, a] }, {}])).toEqual([a, b]);
  });

  it("ships the logo file Google publishes, unmodified", () => {
    // SHA-256 of GoogleMaps_Logo_Gray.svg in Google_Maps_Attribution_Assets.zip, 2026-09-25.
    const svg = readFileSync(join(process.cwd(), "public", GOOGLE_MAPS_LOGO));
    expect(createHash("sha256").update(svg).digest("hex")).toBe(
      "99a08b570afce8ce830d26aeb93ba287f6ad67387e6219fe381234ad7e4016b5",
    );
  });
});

/** Every screen that shows Places data mounts the attribution (spec v0.11.1). */
describe("the attribution is on every screen that shows Places data", () => {
  const screens = [
    "review/QueueTable.tsx",
    "review/BusinessFacts.tsx",
    "review/DecisionDialog.tsx",
    "leads/Leads.tsx",
    "leads/LeadDetail.tsx",
    "duplicates/Duplicates.tsx",
    "crm/CrmDashboard.tsx",
    "admin/Suppressions.tsx",
  ];
  it.each(screens)("%s", (file) => {
    const source = readFileSync(join(process.cwd(), "src", "components", file), "utf8");
    expect(source).toContain("<GoogleMapsAttribution");
  });
});
