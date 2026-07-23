import { describe, expect, it } from "vitest";
import type { JuanMarker } from "../../../api/types";
import { buildBlocks, buildRenderedChars } from "../TextViewer";

function blockText(
  bodyText: string,
  markers: JuanMarker[],
): string[] {
  const chars = buildRenderedChars(bodyText, markers, "phrase", "canonical");
  return buildBlocks("body", chars, markers, "phrase", [...bodyText].length)
    .map((block) => block.chars.map((char) => char.ch).join(""));
}

describe("TextViewer phrase blocks", () => {
  it("keeps injected trailing punctuation with the preceding phrase", () => {
    const markers: JuanMarker[] = [
      { type: "tls:seg", offset: 2 },
      { type: "punctuation", offset: 2, content: "，！」。：．)/" },
    ];

    expect(blockText("甲乙丙丁", markers)).toEqual([
      "甲乙，！」。：．)/",
      "丙丁",
    ]);
  });

  it("keeps literal trailing punctuation with the preceding phrase", () => {
    expect(blockText("甲乙，！」。：．)/丙丁", [])).toEqual([
      "甲乙，！」。：．)/",
      "丙丁",
    ]);
  });

  it("labels rendered chars with their voice", () => {
    const markers: JuanMarker[] = [
      { type: "voice", offset: 1, length: 2, name: "note" },
      { type: "voice", offset: 2, length: 1, name: "emphasis" },
    ];

    expect(buildRenderedChars("甲乙丙丁", markers, "phrase", "canonical")
      .map((char) => char.voice)).toEqual([
        "default",
        "note",
        "emphasis",
        "default",
      ]);
  });
});
