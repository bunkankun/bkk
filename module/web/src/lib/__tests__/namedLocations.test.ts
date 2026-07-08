import { describe, expect, it } from "vitest";
import {
  locationFilePath,
  selectionContentPreview,
  serializeSavedLocation,
  subLocationOffset,
} from "../namedLocations";

describe("named locations", () => {
  it("formats location file paths with compact UTC timestamps", () => {
    expect(locationFilePath(new Date("2026-07-07T03:04:05.006Z"))).toBe(
      "locations/20260707T030405006.yaml",
    );
  });

  it("keeps short content and truncates long content", () => {
    expect(selectionContentPreview(["寒", "山"])).toBe("寒山");
    const chars = Array.from({ length: 205 }, (_, i) => String(i % 10));
    expect(selectionContentPreview(chars)).toHaveLength(203);
    expect(selectionContentPreview(chars)).toContain("...");
  });

  it("emits relative sub-selection offsets", () => {
    expect(subLocationOffset(3, 5)).toBe("@3+2");
    expect(subLocationOffset(5, 3)).toBe("@3+0");
  });

  it("serializes a constrained saved-location YAML document", () => {
    expect(
      serializeSavedLocation({
        id: "loc-1",
        location: "KR3d0004/1/body/@36+2",
        date: "2026-07-07T03:04:05.006Z",
        content: "寒山",
        title: "Cold Mountain",
        tags: ["poetry", "note:quoted"],
        note: "line 1\nline 2",
        sub: [{ offset: "@1+1", note: "term", content: "山" }],
      }),
    ).toBe(
      [
        '- id: "loc-1"',
        '  location: "KR3d0004/1/body/@36+2"',
        '  date: "2026-07-07T03:04:05.006Z"',
        '  content: "寒山"',
        '  title: "Cold Mountain"',
        "  tags:",
        '    - "poetry"',
        '    - "note:quoted"',
        "  note: |",
        "    line 1",
        "    line 2",
        "  sub:",
        '    - offset: "@1+1"',
        '      note: "term"',
        '      content: "山"',
        "",
      ].join("\n"),
    );
  });
});
