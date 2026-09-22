import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("says how much is on screen, in the singular when there is one", () => {
    const { rerender } = render(<Pagination count={1} noun={["business", "businesses"]} />);
    expect(screen.getByText("1 business")).toBeTruthy();
    rerender(<Pagination count={4} noun={["business", "businesses"]} />);
    expect(screen.getByText("4 businesses")).toBeTruthy();
  });

  it("offers more only when there is more, and not while loading", () => {
    const onLoadMore = vi.fn();
    const { rerender } = render(<Pagination count={20} noun={["lead", "leads"]} />);
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    rerender(<Pagination count={20} noun={["lead", "leads"]} hasMore onLoadMore={onLoadMore} />);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(onLoadMore).toHaveBeenCalled();
    rerender(<Pagination count={20} noun={["lead", "leads"]} hasMore loading onLoadMore={onLoadMore} />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Load more" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
