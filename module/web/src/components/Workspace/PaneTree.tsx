// PaneTree is the host for one or more workspace panes. v1 only ever
// renders a single leaf — but the indirection lives here so that a
// future slice can render `pane.kind === 'split'` cases without
// touching App.tsx.

import { WorkspacePane } from "./WorkspacePane";

export function PaneTree() {
  // v1: single leaf. The workspace store still uses a PaneLeaf shape
  // that could later be wrapped in a discriminated union for splits.
  return (
    <div className="ca">
      <WorkspacePane />
    </div>
  );
}
