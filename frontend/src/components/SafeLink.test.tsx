import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SafeLink } from "./SafeLink";

describe("SafeLink", () => {
  it("renders an http(s) URL as a new-tab link with rel noopener noreferrer", () => {
    render(<SafeLink href="https://example.com/page" />);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("https://example.com/page");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.textContent).toBe("example.com");
  });

  it("renders a javascript: URL as text, never as a link", () => {
    const { container } = render(<SafeLink href="javascript:alert(1)" />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.textContent).toBe("javascript:alert(1)");
    expect(container.querySelector("a")).toBeNull();
  });

  it("renders nothing for an empty value", () => {
    const { container } = render(<SafeLink href={null} />);
    expect(container.textContent).toBe("");
  });
});
