import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("names the next action, so an empty screen is never a dead end", () => {
    render(
      <EmptyState
        title="Nothing to review."
        description="Run a search to find businesses."
        action={<a href="https://example.test/searches">Go to Searches</a>}
      />,
    );
    expect(screen.getByText("Nothing to review.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to Searches" }).getAttribute("href")).toBe("https://example.test/searches");
  });

  it("works without an action for a state nobody can act on", () => {
    render(<EmptyState title="No sync attempt yet." />);
    expect(screen.getByText("No sync attempt yet.")).toBeTruthy();
  });
});
