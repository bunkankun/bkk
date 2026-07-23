import { describe, expect, it } from "vitest";
import type { Manifest } from "../../../api/types";
import { buildLocalItems } from "../Toc";

function manifest(tableOfContents: Manifest["table_of_contents"]): Manifest {
  return {
    canonical_identifier: "bkk:test/TEST/v1",
    canonical_location: "https://example.test/TEST/v1",
    canonical_set: { identifier: "bkk:charset/cjk-v1", hash: "sha256:test" },
    assets: { parts: [] },
    table_of_contents: tableOfContents,
    metadata: {},
    hash: "sha256:test",
  };
}

describe("buildLocalItems", () => {
  it("expands empty spans to the next TOC entry on the same level", () => {
    const items = buildLocalItems(
      manifest([
        {
          ref: { seq: 1, marker_id: "a", span: ["body", 10, 10] },
          label: "甲",
          level: 1,
        },
        {
          ref: { seq: 1, marker_id: "b", span: ["body", 25, 25] },
          label: "乙",
          level: 2,
        },
        {
          ref: { seq: 1, marker_id: "c", span: ["body", 40, 40] },
          label: "丙",
          level: 1,
        },
      ]),
      1,
    );

    expect(items.map((item) => [item.label, item.start, item.end])).toEqual([
      ["甲", 10, 40],
      ["乙", 25, 25],
      ["丙", 40, 40],
    ]);
  });

  it("does not infer spans across buckets", () => {
    const items = buildLocalItems(
      manifest([
        {
          ref: { seq: 1, marker_id: "front", span: ["front", 0, 0] },
          label: "序",
          level: 1,
        },
        {
          ref: { seq: 1, marker_id: "body", span: ["body", 12, 12] },
          label: "本文",
          level: 1,
        },
      ]),
      1,
    );

    expect(items.map((item) => [item.bucket, item.start, item.end])).toEqual([
      ["front", 0, 0],
      ["body", 12, 12],
    ]);
  });
});
