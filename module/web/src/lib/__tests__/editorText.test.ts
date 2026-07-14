import { describe, expect, it } from "vitest";
import type { EditableMarker } from "../editSplices";
import {
  DEFAULT_PUNCTUATION_SET,
  canonicalSelectionFromDom,
  editorPositionAt,
  markerDomSelection,
  parsePunctuatedText,
  punctuationInputAllowed,
  reconcilePunctuationMarkers,
  renderEditorText,
} from "../editorText";

function marker(
  key: string,
  data: EditableMarker["data"],
  originalId: string | null = null,
): EditableMarker {
  return { key, data, originalId, unresolved: false };
}

describe("renderEditorText", () => {
  it("maps codepoint offsets across supplementary characters and punctuation", () => {
    const punctuation = marker("p1", {
      type: "punctuation",
      offset: 1,
      content: "。",
      id: "T_X_001-bkkpn1",
    });
    const view = renderEditorText("𠀀甲", [punctuation], DEFAULT_PUNCTUATION_SET);

    expect(view.text).toBe("𠀀。甲");
    expect(view.caretBefore).toEqual([0, 2, 4]);
    expect(view.charStart).toEqual([0, 3, 4]);
    expect(markerDomSelection(view, punctuation)).toEqual({ start: 2, end: 3 });
    expect(editorPositionAt(view, 3)).toEqual({
      offset: 1,
      ch: "甲",
      cp: 0x7532,
    });
  });

  it("selects a canonical marker range without trailing punctuation", () => {
    const view = renderEditorText(
      "甲乙丙",
      [marker("p", { type: "punctuation", offset: 1, content: "，" })],
      DEFAULT_PUNCTUATION_SET,
    );
    const range = marker("r", { type: "voice", offset: 0, length: 2 });
    expect(markerDomSelection(view, range)).toEqual({ start: 0, end: 3 });
    expect(canonicalSelectionFromDom(view, 0, 3)).toEqual({
      offset: 0,
      length: 2,
    });
  });

  it("can include layout markers with punctuation display", () => {
    const lineBreak = marker("lb", { type: "line-break", offset: 1, id: "line-id" });
    const view = renderEditorText(
      "甲乙",
      [
        lineBreak,
        marker("indent", { type: "indent", offset: 1, content: "　" }),
        marker("comma", { type: "punctuation", offset: 1, content: "，" }),
      ],
      DEFAULT_PUNCTUATION_SET,
      true,
    );

    expect(view.text).toBe("甲\n　，乙");
    expect(markerDomSelection(view, lineBreak)).toEqual({ start: 1, end: 2 });
    expect(view.units.map((unit) => unit.kind)).toEqual([
      "text",
      "layout",
      "layout",
      "punctuation",
      "text",
    ]);
  });
});

describe("parsePunctuatedText", () => {
  it("extracts punctuation without changing canonical offsets", () => {
    const result = parsePunctuatedText("甲乙𠀀", "「甲，乙𠀀。」");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect([...result.punctuation]).toEqual([
        [0, "「"],
        [1, "，"],
        [3, "。」"],
      ]);
    }
  });

  it("rejects canonical insertion, deletion, and replacement", () => {
    expect(parsePunctuatedText("甲乙", "甲丙乙").ok).toBe(false);
    expect(parsePunctuatedText("甲乙", "甲").ok).toBe(false);
    expect(parsePunctuatedText("甲乙", "丙乙").ok).toBe(false);
    expect(punctuationInputAllowed("甲乙", "甲abc乙")).toBe(false);
    expect(punctuationInputAllowed("甲乙", "甲，乙")).toBe(true);
    expect(punctuationInputAllowed("甲a乙", "甲乙")).toBe(true);
  });
});

describe("reconcilePunctuationMarkers", () => {
  it("preserves unchanged prefix and suffix markers around an insertion", () => {
    const existing = [
      marker(
        "comma",
        { type: "punctuation", offset: 1, content: "，", id: "comma-id" },
        "comma-id",
      ),
      marker("line", {
        type: "line-break",
        offset: 1,
        id: "line-id",
      }),
      marker(
        "stop",
        { type: "punctuation", offset: 1, content: "。", id: "stop-id" },
        "stop-id",
      ),
      marker("named", {
        type: "punctuation",
        offset: 1,
        content: "！",
        id: "named-id",
        set: "modern",
      }),
    ];
    const result = reconcilePunctuationMarkers(
      existing,
      DEFAULT_PUNCTUATION_SET,
      new Map([[1, "，、。"]]),
      () => "new",
    );

    expect(result.map((item) => item.key)).toEqual([
      "comma",
      "line",
      "new",
      "stop",
      "named",
    ]);
    expect(result.find((item) => item.key === "new")?.data).toEqual({
      type: "punctuation",
      offset: 1,
      content: "、",
      id: "",
    });
    expect(result.find((item) => item.key === "new")?.generatedId).toBe(true);
  });

  it("does not reorder unrelated markers when punctuation is replaced", () => {
    const existing = [
      marker("head", { type: "head", offset: 0, id: "head-id" }),
      marker("old", {
        type: "punctuation",
        offset: 0,
        content: "，",
        id: "old-id",
      }),
      marker("line", { type: "line-break", offset: 0, id: "line-id" }),
      marker("page", { type: "page-break", offset: 0, id: "page-id" }),
    ];
    const result = reconcilePunctuationMarkers(
      existing,
      DEFAULT_PUNCTUATION_SET,
      new Map([[0, "。"]]),
      () => "replacement",
    );

    expect(result.map((item) => item.key)).toEqual([
      "head",
      "replacement",
      "line",
      "page",
    ]);
  });
});
