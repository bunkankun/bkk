import {
  useWorkspace,
  workspace,
  type LineBreakDisplay,
  type LineMode,
  type PaneNode,
  type ReadMode,
} from "../state/useWorkspace";
import { krClass } from "../lib/krClass";

function formatCp(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

const MODES: { id: ReadMode; label: string; enabled: boolean; tip: string }[] = [
  { id: "read", label: "Read", enabled: true, tip: "Read mode" },
  { id: "trans", label: "Trans", enabled: true, tip: "Translation mode" },
  { id: "inspect", label: "Inspect", enabled: true, tip: "Inspect mode (image + text)" },
];

const LINE_MODES: { id: LineMode; label: string; tip: string }[] = [
  { id: "paragraph", label: "P", tip: "Paragraph display" },
  { id: "phrase", label: "L", tip: "Phrase-per-line display (tls:seg or punctuation)" },
];

const LB_MODES: { id: LineBreakDisplay; label: string; tip: string }[] = [
  { id: "off", label: "—", tip: "Hide source line-breaks" },
  { id: "glyph", label: "·", tip: "Mark source line-breaks with ¶" },
  { id: "br", label: "↵", tip: "Wrap source line-breaks (real <br>)" },
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
  const pane = useWorkspace((s) => s.pane);
  const focusedPaneId = useWorkspace((s) => s.focusedPaneId);
  const defaultMode = useWorkspace((s) => s.readMode);
  const defaultLineMode = useWorkspace((s) => s.readPrefs.lineMode);
  const showPageBreaks = useWorkspace((s) => s.readPrefs.showPageBreaks);
  const lineBreakDisplay = useWorkspace((s) => s.readPrefs.lineBreakDisplay);
  const tab = focusedTab(pane, focusedPaneId);
  const textTab = tab?.type === "text" ? tab : null;
  const mode = textTab?.readMode ?? defaultMode;
  const lineMode = textTab?.lineMode ?? defaultLineMode;
  const cp = textTab?.hoverCodepoint ?? null;

  return (
    <div className="sb">
      <div className={`si${textid ? ` ${krClass(textid)}` : ""}`}>{textid ?? "—"}</div>
      <div className="si">juan {seq ?? "—"}</div>
      <div className="si">{cp != null ? formatCp(cp) : ""}</div>
      <div className="s-sp" />
      <button
        className={`sdb${showPageBreaks ? " on" : ""}`}
        title="Show page-breaks"
        onClick={() => workspace.setShowPageBreaks(!showPageBreaks)}
      >
        ⌐
      </button>
      {LB_MODES.map((m) => (
        <button
          key={m.id}
          className={`sdb${lineBreakDisplay === m.id ? " on" : ""}`}
          title={m.tip}
          onClick={() => workspace.setLineBreakDisplay(m.id)}
        >
          {m.label}
        </button>
      ))}
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
