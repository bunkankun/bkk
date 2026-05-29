import {
  useWorkspace,
  workspace,
  type LineMode,
  type PaneNode,
  type ReadMode,
} from "../state/useWorkspace";

function formatCp(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

const MODES: { id: ReadMode; label: string; enabled: boolean; tip: string }[] = [
  { id: "read", label: "Read", enabled: true, tip: "Read mode" },
  { id: "trans", label: "Trans", enabled: false, tip: "Translation mode (v2)" },
  { id: "inspect", label: "Inspect", enabled: true, tip: "Inspect mode (image + text)" },
];

const LINE_MODES: { id: LineMode; label: string; tip: string }[] = [
  { id: "paragraph", label: "¶", tip: "Paragraph display" },
  { id: "phrase", label: "↵", tip: "Phrase-per-line display (tls:seg or punctuation)" },
];

function paneLeaves(pane: PaneNode): Extract<PaneNode, { kind: "leaf" }>[] {
  return pane.kind === "leaf" ? [pane] : pane.children.flatMap(paneLeaves);
}

function focusedTab(pane: PaneNode, focusedPaneId: string | null) {
  const leaves = paneLeaves(pane);
  const leaf = leaves.find((item) => item.id === focusedPaneId) ?? leaves[0] ?? null;
  return leaf?.tabs.find((tab) => tab.id === leaf.activeTabId) ?? leaf?.tabs[0] ?? null;
}

export function StatusBar() {
  const textid = useWorkspace((s) => s.activeTextid);
  const seq = useWorkspace((s) => s.activeSeq);
  const cp = useWorkspace((s) => s.hoverCodepoint);
  const pane = useWorkspace((s) => s.pane);
  const focusedPaneId = useWorkspace((s) => s.focusedPaneId);
  const defaultMode = useWorkspace((s) => s.readMode);
  const defaultLineMode = useWorkspace((s) => s.readPrefs.lineMode);
  const tab = focusedTab(pane, focusedPaneId);
  const mode = tab?.readMode ?? defaultMode;
  const lineMode = tab?.lineMode ?? defaultLineMode;

  return (
    <div className="sb">
      <div className="si">{textid ?? "—"}</div>
      <div className="si">juan {seq ?? "—"}</div>
      <div className="si">{cp != null ? formatCp(cp) : ""}</div>
      <div className="s-sp" />
      {LINE_MODES.map((m) => (
        <button
          key={m.id}
          className={`sdb${lineMode === m.id ? " on" : ""}`}
          title={m.tip}
          onClick={() => workspace.setLineMode(m.id)}
        >
          {m.label}
        </button>
      ))}
      {MODES.map((m) => (
        <button
          key={m.id}
          className={`sdb${mode === m.id ? " on" : ""}`}
          disabled={!m.enabled}
          title={m.tip}
          onClick={() => m.enabled && workspace.setReadMode(m.id)}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}
