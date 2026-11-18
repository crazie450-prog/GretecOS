// The Costs view's caps card (WO-11.2, D334): today against the day cap, tasks against the task cap.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { CostCaps } from "../src/api/types";
import { Caps } from "../src/views/Costs";

afterEach(cleanup);

const caps = (over: Partial<CostCaps> = {}): CostCaps => ({
  day: { start: "2026-11-15T00:00:00+00:00", spent_usd: 1.5, calls: 3, cap_usd: 10, pct: 15, reached: false },
  task: { cap_usd: 2, reached: 0, top: [{ task: "t1", usd: 1.5, calls: 3, reached: false }] },
  notice_pct: [50],
  crossed_notice: [],
  ...over,
});

describe("the caps card", () => {
  it("shows today within the day cap and no task at its cap", () => {
    render(<Caps caps={caps()} />);
    expect(screen.getByTestId("day-spent").textContent).toContain("1.50");
    expect(screen.getByText("within the day cap").closest(".gos-status")?.getAttribute("data-status")).toBe("ok");
    expect(screen.getByText("none reached today")).toBeTruthy();
    expect(screen.getByText(/heads-up at 50% of the credits/)).toBeTruthy();
  });
  it("marks the day cap and the tasks that reached theirs", () => {
    render(
      <Caps
        caps={caps({
          day: { start: "x", spent_usd: 12, calls: 9, cap_usd: 10, pct: 120, reached: true },
          task: { cap_usd: 2, reached: 1, top: [{ task: "design-7", usd: 2.5, calls: 4, reached: true }] },
          crossed_notice: [50],
        })}
      />,
    );
    expect(screen.getByText("day cap reached").closest(".gos-status")?.getAttribute("data-status")).toBe("warn");
    expect(screen.getByText("1 reached it today")).toBeTruthy();
    expect(screen.getByText("design-7").closest(".gos-status")?.getAttribute("data-status")).toBe("warn");
    expect(screen.getByText(/past 50%/)).toBeTruthy();
  });
});
