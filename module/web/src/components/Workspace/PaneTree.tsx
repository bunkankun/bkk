import { useWorkspace, type PaneNode } from "../../state/useWorkspace";
import { WorkspacePane } from "./WorkspacePane";

function PaneNodeView({ pane }: { pane: PaneNode }) {
  if (pane.kind === "leaf") return <WorkspacePane pane={pane} />;
  return (
    <div className="pane-split pane-split-horizontal">
      {pane.children.map((child) => (
        <PaneNodeView key={child.id} pane={child} />
      ))}
    </div>
  );
}

export function PaneTree() {
  const pane = useWorkspace((s) => s.pane);
  return (
    <div className="ca">
      <PaneNodeView pane={pane} />
    </div>
  );
}
