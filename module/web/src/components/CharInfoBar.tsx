import { useWorkspace } from "../state/useWorkspace";

function formatCp(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

export function CharInfoBar() {
  const ch = useWorkspace((s) => s.hoverChar);
  const cp = useWorkspace((s) => s.hoverCodepoint);
  if (!ch || cp == null) {
    return (
      <div className="cib">
        <span className="cib-empty">Hover a character to see its codepoint.</span>
      </div>
    );
  }
  return (
    <div className="cib">
      <span className="cib-g">{ch}</span>
      <span className="cib-cp">{formatCp(cp)}</span>
    </div>
  );
}
