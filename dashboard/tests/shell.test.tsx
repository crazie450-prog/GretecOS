// The shell card (WO-10.7, D316): the core's preview mesh drawn as an isometric picture from data
// only, the palette manifest with registry names, the Lab-only paste steps; and the projection.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import valid from "../../tests/fixtures/cards/valid.json";
import { Card } from "../src/cards/Card";
import { CardContext } from "../src/cards/context";
import { corners, project, turn } from "../src/cards/shellMesh";
import type { ShellCard } from "../src/cards/types.gen";

afterEach(cleanup);

const SHELL = valid.shell as ShellCard;

function draw(card: ShellCard) {
  return render(
    <CardContext.Provider value={{ stale: false }}>
      <Card card={card} />
    </CardContext.Provider>,
  );
}

describe("the shell mesh", () => {
  it("puts a face on the right side of its cell", () => {
    expect(corners([2, 0, 0, 0, 1, 1, 0])).toEqual([
      [0, 1, 0],
      [1, 1, 0],
      [1, 1, 1],
      [0, 1, 1],
    ]); // the top of cell 0,0,0 is at y 1
    expect(corners([1, 3, 0, 0, 2, 1, 0])[0]).toEqual([3, 0, 0]); // a west face at x 3
    expect(corners([0, 3, 0, 0, 2, 1, 0])[0]).toEqual([4, 0, 0]); // an east face at x 4
  });

  it("keeps only the faces seen from above the south-east, far to near", () => {
    const cube = [0, 1, 2, 3, 4, 5].map((d) => [d, 0, 0, 0, 1, 1, 0]);
    const { polys } = project(cube, [1, 1, 1]);
    expect(polys.map((p) => p.shade).sort()).toEqual(["side-a", "side-b", "top"]);
    for (let i = 1; i < polys.length; i++) expect(polys[i].depth).toBeGreaterThanOrEqual(polys[i - 1].depth);
    // a quarter turn shows another pair of sides, still three faces
    expect(project(cube, [1, 1, 1], 1).polys).toHaveLength(3);
    expect(turn([1, 0, 0], [0, 0, 0], 4)).toEqual([1, 0, 0]);
  });
});

describe("the shell card", () => {
  it("draws the preview, the palette and the paste steps", () => {
    draw(SHELL);
    const svg = screen.getByTestId("shell-preview");
    const shown = Number(svg.getAttribute("data-faces"));
    expect(shown).toBeGreaterThan(0);
    expect(shown).toBeLessThan(SHELL.faces.length); // the hidden sides aren't drawn
    expect(svg.querySelectorAll("polygon.role-light")).not.toHaveLength(0);
    const rows = screen.getByTestId("shell-palette").querySelectorAll("tr");
    expect(rows).toHaveLength(SHELL.palette.length);
    expect(rows[0].textContent).toContain("gregtech:gt.blockconcretes@3");
    expect(screen.getByTestId("shell-status").textContent).toContain("accepted as designed");
    expect(screen.getByTestId("shell-paste").textContent).toContain("Lab only");
  });

  it("turns the view and marks a repaired shell", () => {
    draw({ ...SHELL, status: "repaired", source: "procedural" });
    const before = screen.getByTestId("shell-preview").getAttribute("aria-label");
    fireEvent.click(screen.getByTestId("shell-turn"));
    expect(screen.getByTestId("shell-preview").getAttribute("aria-label")).not.toBe(before);
    expect(screen.getByTestId("shell-status").textContent).toContain("repaired");
  });

  it("refuses a card with a bad build id (the schema check)", () => {
    const { container } = draw({ ...SHELL, build: "sh-NOPE" });
    expect(container.querySelector("[data-card=shell]")).toBeNull();
  });
});
