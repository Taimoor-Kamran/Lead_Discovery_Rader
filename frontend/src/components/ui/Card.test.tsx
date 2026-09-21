import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Card } from "./Card";

describe("Card", () => {
  it("is a section with the surface radius and a line, never a shadow", () => {
    render(<Card aria-label="Business">content</Card>);
    const card = screen.getByLabelText("Business");
    expect(card.tagName).toBe("SECTION");
    expect(card.className).toContain("rounded-lg");
    expect(card.className).toContain("border-line");
    expect(card.className).not.toContain("shadow");
  });

  it("can drop its padding and change its element", () => {
    render(
      <Card as="div" padded={false} aria-label="Table holder">
        x
      </Card>,
    );
    const card = screen.getByLabelText("Table holder");
    expect(card.tagName).toBe("DIV");
    expect(card.className).not.toContain("p-4");
  });
});
