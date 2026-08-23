import { describe, expect, it } from "vitest";
import { NOTE_CLOSE_PUNCT, NOTE_OPEN_PUNCT } from "../punctuationOrder";

describe("punctuationOrder", () => {
  it("exports note boundary punctuation matching the Python renderer", () => {
    expect([...NOTE_OPEN_PUNCT]).toEqual(["(", "（", "「", "『", "《", "〈", "〔", "【"]);
    expect([...NOTE_CLOSE_PUNCT]).toEqual([")", "）", "」", "』", "》", "〉", "〕", "】"]);
  });
});
