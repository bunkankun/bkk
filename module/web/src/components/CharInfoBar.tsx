function formatCp(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, "0")}`;
}

export function CharInfoBar({
  ch,
  cp,
  offset = null,
  bucket = null,
  hoverOffset = null,
  bucketLength = null,
  voice = null,
  onJumpToOffset,
}: {
  ch: string | null;
  cp: number | null;
  offset?: number | null;
  bucket?: string | null;
  hoverOffset?: number | null;
  bucketLength?: number | null;
  voice?: string | null;
  onJumpToOffset?: (bucket: string, offset: number) => void;
}) {
  const canJump =
    bucket != null &&
    hoverOffset != null &&
    bucketLength != null &&
    bucketLength > 0 &&
    onJumpToOffset != null;
  const locationLabel = bucket != null && hoverOffset != null
    ? `${bucket} @ ${hoverOffset}${voice ? ` · ${voice}` : ""}`
    : offset != null
      ? `Offset ${offset}`
      : null;

  const jump = () => {
    if (
      bucket == null ||
      hoverOffset == null ||
      bucketLength == null ||
      bucketLength <= 0 ||
      onJumpToOffset == null
    ) return;
    const maxOffset = bucketLength - 1;
    const raw = window.prompt(
      `Jump to offset in ${bucket} (0-${maxOffset})`,
      String(hoverOffset),
    );
    if (raw == null) return;
    const trimmed = raw.trim();
    if (!/^\d+$/.test(trimmed)) {
      window.alert("Enter a whole-number offset.");
      return;
    }
    const nextOffset = Number(trimmed);
    if (!Number.isSafeInteger(nextOffset) || nextOffset < 0 || nextOffset > maxOffset) {
      window.alert(`Offset must be between 0 and ${maxOffset}.`);
      return;
    }
    onJumpToOffset(bucket, nextOffset);
  };

  if ((!ch || cp == null) && locationLabel == null) {
    return (
      <div className="cib">
        <span className="cib-empty">Hover a character to see its codepoint.</span>
      </div>
    );
  }
  return (
    <div
      className={`cib${canJump ? " cib-jumpable" : ""}`}
      role={canJump ? "button" : undefined}
      tabIndex={canJump ? 0 : undefined}
      title={canJump ? `Click to jump within ${bucket}` : undefined}
      onClick={jump}
      onKeyDown={(event) => {
        if (!canJump || (event.key !== "Enter" && event.key !== " ")) return;
        event.preventDefault();
        jump();
      }}
    >
      {ch && cp != null && <span className="cib-g">{ch}</span>}
      {locationLabel != null && <span className="cib-cp">{locationLabel}</span>}
      {cp != null && <span className="cib-cp">{formatCp(cp)}</span>}
    </div>
  );
}
