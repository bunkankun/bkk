import {
  useWorkspace,
  workspace,
  type LineMode,
  type ReadMode,
} from "../state/useWorkspace";

function formatCp(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

const MODES: { id: ReadMode; label: string; enabled: boolean; tip: string }[] = [
  { id: "read", label: "Read", enabled: true, tip: "Read mode" },
  { id: "trans", label: "Trans", enabled: false, tip: "Translation mode (v2)" },
  { id: "inspect", label: "Inspect", enabled: false, tip: "Inspection mode (v2)" },
];

const LINE_MODES: { id: LineMode; label: string; tip: string }[] = [
  { id: "paragraph", label: "¶", tip: "Paragraph display" },
  { id: "phrase", label: "↵", tip: "Phrase-per-line display (tls:seg or punctuation)" },
];

export function StatusBar() {
  const textid = useWorkspace((s) => s.activeTextid);
  const seq = useWorkspace((s) => s.activeSeq);
  const cp = useWorkspace((s) => s.hoverCodepoint);
  const mode = useWorkspace((s) => s.readMode);
  const lineMode = useWorkspace((s) => s.readPrefs.lineMode);

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
