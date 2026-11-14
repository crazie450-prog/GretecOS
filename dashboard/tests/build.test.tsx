// WO-10.1: the Build view's frame address for a page of the guide export.
import { describe, expect, it } from "vitest";
import { guideSrc } from "../src/views/Build";

describe("guideSrc", () => {
  it("keeps the folders and encodes each segment", () => {
    expect(guideSrc("standin/en_us/multiblock.html")).toBe("/guide/standin/en_us/multiblock.html");
    expect(guideSrc("gt/en_us/a b#?.html")).toBe("/guide/gt/en_us/a%20b%23%3F.html");
  });
});
