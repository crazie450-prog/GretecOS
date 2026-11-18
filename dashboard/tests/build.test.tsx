// WO-10.1: the Build view's frame address for a page of the guide export, and the page it opens.
import { describe, expect, it } from "vitest";
import { firstPage, guideSrc } from "../src/views/Build";

describe("guideSrc", () => {
  it("keeps the folders and encodes each segment", () => {
    expect(guideSrc("standin/en_us/multiblock.html")).toBe("/guide/standin/en_us/multiblock.html");
    expect(guideSrc("gt/en_us/a b#?.html")).toBe("/guide/gt/en_us/a%20b%23%3F.html");
  });
});

describe("firstPage", () => {
  const page = (path: string) => ({ path, title: "", scenes: 1 });
  it("opens GOS's own guide page before the pack's", () => {
    expect(firstPage([page("aaa/en_us/x.html"), page("gos/en_us/index.html")])).toBe("gos/en_us/index.html");
  });
  it("falls back to the first page, or none", () => {
    expect(firstPage([page("aaa/en_us/x.html"), page("gosling/en_us/y.html")])).toBe("aaa/en_us/x.html");
    expect(firstPage([])).toBeNull();
  });
});
