import { describe, expect, it } from "vitest";
import { findTextSplice, transformMarkers, type EditableMarker } from "../editSplices";

function marker(offset: number, length?: number): EditableMarker {
  return {
    key: String(offset),
    originalId: null,
    unresolved: false,
    data: { type: "test", offset, ...(length == null ? {} : { length }) },
  };
}

describe("findTextSplice", () => {
  it("counts Unicode codepoints rather than UTF-16 units", () => {
    expect(findTextSplice("甲𠀀乙", "甲𠀀新乙")).toEqual({
      start: 2, delete_count: 0, insert: "新",
    });
  });

  it("returns a conservative replacement", () => {
    expect(findTextSplice("甲乙丙丁", "甲天地丁")).toEqual({
      start: 1, delete_count: 2, insert: "天地",
    });
  });
});

describe("transformMarkers", () => {
  it("shifts anchors after an insertion and flags an anchor at its boundary", () => {
    const result = transformMarkers([marker(1), marker(3)], {
      start: 1, delete_count: 0, insert: "天地",
    });
    expect(result[0].unresolved).toBe(true);
    expect(result[1].data.offset).toBe(5);
  });

  it("updates a span length for an internal edit", () => {
    const [result] = transformMarkers([marker(1, 5)], {
      start: 3, delete_count: 1, insert: "天地",
    });
    expect(result.unresolved).toBe(false);
    expect(result.data.length).toBe(6);
  });

  it("flags an edit crossing a span boundary", () => {
    const [result] = transformMarkers([marker(2, 3)], {
      start: 1, delete_count: 2, insert: "",
    });
    expect(result.unresolved).toBe(true);
  });
});
