import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Skeleton, SkeletonLines, SkeletonRows, SkeletonTableRows } from "./Skeleton";

describe("Skeleton", () => {
  it("is hidden from assistive technology — it is the shape of what is coming, not content", () => {
    const { container } = render(<Skeleton className="h-4 w-full" />);
    expect(container.querySelector("span")!.getAttribute("aria-hidden")).toBe("true");
  });

  it("draws one block per cell so a table keeps its height while it loads", () => {
    render(<SkeletonRows rows={3} columns={4} />);
    expect(screen.getByTestId("skeleton-rows").querySelectorAll("span")).toHaveLength(12);
  });

  it("draws real rows and cells when it stands in for a table body", () => {
    render(
      <table>
        <tbody>
          <SkeletonTableRows rows={2} columns={3} />
        </tbody>
      </table>,
    );
    expect(screen.getAllByTestId("skeleton-row")).toHaveLength(2);
    expect(screen.getAllByRole("cell")).toHaveLength(6);
  });

  it("draws lines for a detail panel", () => {
    render(<SkeletonLines lines={2} />);
    expect(screen.getByTestId("skeleton-lines").querySelectorAll("span")).toHaveLength(2);
  });
});
