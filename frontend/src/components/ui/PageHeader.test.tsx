import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("is the page's only h1, with its sentence and its actions", () => {
    render(
      <PageHeader
        title="Review queue"
        description="Businesses with open opportunities, strongest first."
        actions={<button type="button">Reject selected</button>}
        meta={<span>Back</span>}
      />,
    );
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("Review queue");
    expect(screen.getByRole("button", { name: "Reject selected" })).toBeTruthy();
    expect(screen.getByText("Back")).toBeTruthy();
  });

  it("puts no eyebrow above the heading", () => {
    render(<PageHeader title="Leads" />);
    const header = screen.getByRole("heading", { level: 1 }).parentElement!;
    expect(header.firstElementChild!.tagName).toBe("H1");
  });
});
