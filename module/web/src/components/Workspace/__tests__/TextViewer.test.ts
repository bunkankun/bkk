import { describe, expect, it } from "vitest";
import type { JuanMarker } from "../../../api/types";
import {
  buildBlocks,
  buildRenderedChars,
  headSequenceLabel,
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
    ).toBe("甲》？；。』」，、：)\n(「『《乙");
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
      "甲乙！。」，：．/)",
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

  it("carries KRP head paths for sequence labels", () => {
    const markers: JuanMarker[] = [
      { type: "voice", offset: 0, length: 2, name: "head", path: [5] },
      { type: "voice", offset: 2, length: 2, name: "head", path: [5, 1] },
    ];
    const chars = buildRenderedChars("甲乙丙丁", markers, "phrase", "canonical");

    expect(
      voiceDisplaySegments(chars).map((segment) => ({
        voice: segment.voice,
        text: segment.chars.map((char) => char.ch).join(""),
        label: headSequenceLabel(12, segment.voicePath),
      })),
    ).toEqual([
      { voice: "head", text: "甲乙", label: "12.5" },
      { voice: "head", text: "丙丁", label: "12.5.1" },
    ]);
  });

  it("keeps a heading note after the heading when the previous note closes at the same offset", () => {
    const markers: JuanMarker[] = [
      { type: "voice", offset: 0, length: 2, name: "note" },
      { type: "punctuation", offset: 0, content: "(" },
      { type: "punctuation", offset: 2, content: ")" },
      { type: "line-break", offset: 2 },
      { type: "indent", offset: 2, content: "　　" },
      { type: "voice", offset: 2, length: 4, name: "head", path: [6] },
      { type: "punctuation", offset: 2, content: "。" },
      { type: "punctuation", offset: 6, content: "(" },
      { type: "punctuation", offset: 6, content: "《" },
      { type: "voice", offset: 6, length: 15, name: "note" },
      { type: "punctuation", offset: 14, content: "/" },
      { type: "punctuation", offset: 21, content: ")" },
      { type: "line-break", offset: 21 },
    ];

    const chars = buildRenderedChars(
      "上關被出濟州河嶽英靈集作初出濟州别城中故人正文",
      markers,
      "phrase",
      "canonical",
    );

    const segments = voiceDisplaySegments(chars).map((segment) => ({
      voice: segment.voice,
      text: segment.chars.map((char) => char.ch).join(""),
      label: headSequenceLabel(9, segment.voicePath),
    }));

    expect(segments.filter((segment) => segment.label === "9.6")).toEqual([
      { voice: "head", text: "　　被出濟州", label: "9.6" },
    ]);
    expect(segments.some((segment) =>
      segment.label === null &&
      segment.text.includes("(《河嶽英靈集作初出/濟州别城中故人)")
    )).toBe(true);
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

  it("keeps same-offset punctuation outside note paren fences", () => {
    const markers: JuanMarker[] = [
      { type: "voice", offset: 1, length: 2, name: "note" },
      { type: "punctuation", offset: 1, content: "：(" },
      { type: "punctuation", offset: 3, content: "。)" },
    ];

    expect(buildRenderedChars("甲乙丙丁", markers, "phrase", "canonical")
      .map((char) => `${char.ch}:${char.noteVoice === true ? "note" : "plain"}`))
      .toEqual([
        "甲:plain",
        "：:plain",
        "(:note",
        "乙:note",
        "丙:note",
        "。:plain",
        "):note",
        "丁:plain",
      ]);
  });
});
