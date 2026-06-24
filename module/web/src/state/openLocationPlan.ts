// Pure decision logic for `openTextLocation`. Kept in its own module so it
// can be unit-tested without loading the workspace store's side effects
// (localStorage reads, fetch, etc.).

import type { PaneNode, PaneLeaf, PaneTab, TextTab } from "./useWorkspace";

export type OpenLocationPlan =
  | { kind: "focus"; leafId: string }
  | { kind: "replace"; leafId: string; oldTabId: string; existing: TextTab }
  | { kind: "open" };

function leaves(node: PaneNode): PaneLeaf[] {
  if (node.kind === "leaf") return [node];
  return node.children.flatMap(leaves);
}

function activeTab(leaf: PaneLeaf): PaneTab | null {
  return leaf.tabs.find((t) => t.id === leaf.activeTabId) ?? leaf.tabs[0] ?? null;
}

export function planOpenTextLocation(
  pane: PaneNode,
  sourceLeafId: string | null,
  tabId: string,
): OpenLocationPlan {
  for (const leaf of leaves(pane)) {
    if (leaf.tabs.some((t) => t.id === tabId)) {
      return { kind: "focus", leafId: leaf.id };
    }
  }
  for (const leaf of leaves(pane)) {
    if (leaf.id === sourceLeafId) continue;
    const active = activeTab(leaf);
    if (active && active.type === "text" && !active.pinned) {
      return { kind: "replace", leafId: leaf.id, oldTabId: active.id, existing: active };
    }
  }
  return { kind: "open" };
}
