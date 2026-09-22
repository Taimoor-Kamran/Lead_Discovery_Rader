import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Table, TableWrap, TBody, Td, Th, THead, Tr } from "./Table";

function renderTable(onSort = vi.fn()) {
  render(
    <TableWrap>
      <Table minWidth="40rem">
        <THead>
          <tr>
            <Th>Business</Th>
            <Th numeric sort="descending" onSort={onSort}>
              Score
            </Th>
          </tr>
        </THead>
        <TBody>
          <Tr selected>
            <Td>Barton Creek Plumbing</Td>
            <Td numeric>72</Td>
          </Tr>
        </TBody>
      </Table>
    </TableWrap>,
  );
  return onSort;
}

describe("Table", () => {
  it("scrolls inside its own container rather than widening the page", () => {
    renderTable();
    const wrap = screen.getByRole("table").parentElement!;
    expect(wrap.className).toContain("overflow-x-auto");
    expect(screen.getByRole("table").style.minWidth).toBe("40rem");
  });

  it("gives a numeric column tabular figures and right alignment", () => {
    renderTable();
    const cell = screen.getByRole("cell", { name: "72" });
    expect(cell.className).toContain("tabular-nums");
    expect(cell.className).toContain("text-right");
    expect(cell.className).toContain("font-mono");
  });

  it("makes a sortable header a real button and reports the direction", () => {
    const onSort = renderTable();
    const header = screen.getByRole("columnheader", { name: /Score/ });
    expect(header.getAttribute("aria-sort")).toBe("descending");
    fireEvent.click(screen.getByRole("button", { name: /Score/ }));
    expect(onSort).toHaveBeenCalled();
  });

  it("marks a selected row without relying on the hover style", () => {
    renderTable();
    expect(screen.getByRole("row", { name: /Barton Creek/ }).className).toContain("bg-accent-tint");
  });
});
