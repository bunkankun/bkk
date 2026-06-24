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

  it("replaces a non-pinned text tab when juan not open and source is pinned core-record", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: true })]),
      leaf("L2", [textTab("textA:1")]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan.kind).toBe("replace");
    if (plan.kind !== "replace") throw new Error("unreachable");
    expect(plan.leafId).toBe("L2");
    expect(plan.oldTabId).toBe("textA:1");
  });

  it("replaces a non-pinned text tab even when source is a non-pinned core-record", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: false })]),
      leaf("L2", [textTab("textA:1")]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan.kind).toBe("replace");
    if (plan.kind !== "replace") throw new Error("unreachable");
    expect(plan.leafId).toBe("L2");
  });

  it("returns open when there is no replaceable text tab", () => {
    const pane = split(
      leaf("L1", [coreTab("c1", { pinned: true })]),
      leaf("L2", [textTab("textA:1", { pinned: true })]),
    );
    const plan = planOpenTextLocation(pane, "L1", "textB:2");
    expect(plan).toEqual({ kind: "open" });
  });

  it("does not replace the source leaf itself", () => {
    const pane: PaneNode = leaf("only", [textTab("textA:1")]);
    const plan = planOpenTextLocation(pane, "only", "textB:2");
    expect(plan).toEqual({ kind: "open" });
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
