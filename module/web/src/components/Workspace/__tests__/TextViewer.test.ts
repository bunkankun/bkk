import { describe, expect, it } from "vitest";
import type { JuanMarker } from "../../../api/types";
import {
  buildBlocks,
  buildRenderedChars,
  voiceDisplaySegments,
} from "../TextViewer";

function blockText(
  bodyText: string,
  markers: JuanMarker[],
): string[] {
  const chars = buildRenderedChars(bodyText, markers, "phrase", "canonical");
  return buildBlocks("body", chars, markers, "phrase", [...bodyText].length)
    .map((block) => block.chars.map((char) => char.ch).join(""));
}

describe("TextViewer phrase blocks", () => {
  it("orders same-offset injected punctuation for smooth reading", () => {
    const markers: JuanMarker[] = [
      { type: "punctuation", offset: 1, content: "《『「(：\n)」』、，。；？》" },
    ];

    expect(
      buildRenderedChars("甲乙", markers, "phrase", "canonical")
        .map((char) => char.ch)
        .join(""),
    ).toBe("甲》？；。，、』」：)\n(「『《乙");
  });

  it("orders page breaks and indents with same-offset punctuation", () => {
    const markers: JuanMarker[] = [
      { type: "indent", offset: 1, content: "　　" },
      { type: "page-break", offset: 1, id: "TEST_WYG_001-1a" },
      { type: "punctuation", offset: 1, content: "。\n)/》" },
    ];

    expect(
      buildRenderedChars("甲乙", markers, "phrase", "canonical")
        .map((char) => char.pageAnchor ? "<pb>" : char.ch)
        .join(""),
    ).toBe("甲》。/)\n<pb>　　乙");
  });

  it("keeps injected trailing punctuation with the preceding phrase", () => {
    const markers: JuanMarker[] = [
      { type: "tls:seg", offset: 2 },
      { type: "punctuation", offset: 2, content: "，！」。：．)/" },
    ];

    expect(blockText("甲乙丙丁", markers)).toEqual([
      "甲乙，！。」：．/)",
      "丙丁",
    ]);
  });

  it("keeps literal trailing punctuation with the preceding phrase", () => {
    expect(blockText("甲乙，！」。：．)/丙丁", [])).toEqual([
      "甲乙，！」。：．)/",
      "丙丁",
    ]);
  });

  it("does not start a phrase line with an ASCII close parenthesis", () => {
    const markers: JuanMarker[] = [
      { type: "tls:seg", offset: 2 },
      { type: "tls:seg", offset: 3 },
    ];

    expect(blockText("甲乙)丙丁", markers)).toEqual([
      "甲乙)",
      "丙丁",
    ]);
  });

  it("keeps leading dictionary definition closers with the previous voice line", () => {
    const markers: JuanMarker[] = [
      { type: "voice", offset: 0, length: 2, name: "lemma" },
      { type: "punctuation", offset: 2, content: "/)" },
      { type: "voice", offset: 2, length: 2, name: "def" },
    ];
    const chars = buildRenderedChars("甲乙丙丁", markers, "phrase", "canonical");

    expect(
      voiceDisplaySegments(chars).map((segment) => ({
        voice: segment.voice,
        text: segment.chars.map((char) => char.ch).join(""),
      })),
    ).toEqual([
      { voice: "lemma", text: "甲乙/)" },
      { voice: "def", text: "丙丁" },
    ]);
  });

  it("renders root and commentary voices as display segments", () => {
    const markers: JuanMarker[] = [
      { type: "voice", offset: 0, length: 2, name: "root" },
      { type: "punctuation", offset: 2, content: "。" },
      { type: "voice", offset: 2, length: 2, name: "commentary" },
    ];
    const chars = buildRenderedChars("甲乙丙丁", markers, "phrase", "canonical");

    expect(
      voiceDisplaySegments(chars).map((segment) => ({
        voice: segment.voice,
        text: segment.chars.map((char) => char.ch).join(""),
      })),
    ).toEqual([
      { voice: "root", text: "甲乙。" },
      { voice: "commentary", text: "丙丁" },
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
