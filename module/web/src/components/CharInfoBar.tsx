function formatCp(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

export function CharInfoBar({ ch, cp }: { ch: string | null; cp: number | null }) {
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
