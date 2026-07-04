function formatCp(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

export function CharInfoBar({
  ch,
  cp,
  offset = null,
}: {
  ch: string | null;
  cp: number | null;
  offset?: number | null;
}) {
  if ((!ch || cp == null) && offset == null) {
    return (
      <div className="cib">
        <span className="cib-empty">Hover a character to see its codepoint.</span>
      </div>
    );
  }
  return (
    <div className="cib">
      {ch && cp != null && <span className="cib-g">{ch}</span>}
      {offset != null && <span className="cib-cp">Offset {offset}</span>}
      {cp != null && <span className="cib-cp">{formatCp(cp)}</span>}
    </div>
  );
}
