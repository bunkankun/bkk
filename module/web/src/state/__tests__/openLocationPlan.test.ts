import { describe, it, expect } from "vitest";
import { planOpenTextLocation } from "../openLocationPlan";
import type { PaneNode, PaneLeaf, TextTab, CoreRecordTab } from "../useWorkspace";

function textTab(id: string, opts: { pinned?: boolean } = {}): TextTab {
  const [textid, seqStr] = id.split(":");
  return {
    id,
    type: "text",
    textid,
    seq: Number(seqStr ?? 1),
    pinned: opts.pinned ?? false,
  };
}

function coreTab(id: string, opts: { pinned?: boolean } = {}): CoreRecordTab {
  return {
    id,
    type: "core-record",
    collection: "word-relations",
    uuid: id,
    pinned: opts.pinned ?? false,
  };
}

function leaf(id: string, tabs: Array<TextTab | CoreRecordTab>): PaneLeaf {
  return { kind: "leaf", id, tabs, activeTabId: tabs[0]?.id ?? null };
}

function split(...children: PaneNode[]): PaneNode {
  return { kind: "split", id: "root-split", direction: "horizontal", children };
}

describe("planOpenTextLocation", () => {
  it("focuses an existing tab when the target juan is already open", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: true })]),
      leaf("L2", [textTab("textA:1")]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textA:1");
    expect(plan).toEqual({ kind: "focus", leafId: "L2" });
  });

  it("opens a new pane when the source is not a replaceable text tab", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: true })]),
      leaf("L2", [textTab("textA:1")]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan).toEqual({ kind: "open" });
  });

  it("does not steal an unrelated unpinned text pane", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: false })]),
      leaf("L2", [textTab("textA:1")]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan).toEqual({ kind: "open" });
  });

  it("replaces the source text pane when it is not pinned", () => {
    const pane = split(
      leaf("L1", [textTab("textA:1")]),
      leaf("L2", [textTab("textC:1", { pinned: true })]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan.kind).toBe("replace");
    if (plan.kind !== "replace") throw new Error("unreachable");
    expect(plan.leafId).toBe("L1");
    expect(plan.oldTabId).toBe("textA:1");
  });

  it("returns open when there is no replaceable text tab", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: true })]),
      leaf("L2", [textTab("textA:1", { pinned: true })]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan).toEqual({ kind: "open" });
  });

  it("replaces the source leaf itself when it is an unpinned text pane", () => {
    const pane: PaneNode = leaf("only", [textTab("textA:1")]);
    const plan = planOpenTextLocation(pane, "only", "textB:2");
    expect(plan.kind).toBe("replace");
    if (plan.kind !== "replace") throw new Error("unreachable");
    expect(plan.leafId).toBe("only");
    expect(plan.oldTabId).toBe("textA:1");
  });

  it("ignores a pinned text tab and falls through to open when no unpinned candidate", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: true })]),
      leaf("L2", [textTab("textA:1", { pinned: true })]),
      leaf("L3", [coreTab("c2", { pinned: false })]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan).toEqual({ kind: "open" });
  });
});
