import { useWorkspace, workspace, type TextHistoryEntry } from "../../state/useWorkspace";
import { krClass } from "../../lib/krClass";

function formatVisitedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function pageLabel(entry: TextHistoryEntry): string {
  if (entry.currentPage?.markerId) return `juan ${entry.seq} · ${entry.currentPage.markerId}`;
  return `juan ${entry.seq}`;
}

export function History() {
  const history = useWorkspace((s) => s.textHistory);
  const activeTextid = useWorkspace((s) => s.activeTextid);

  if (history.length === 0) {
    return <div className="empty">No visited texts yet.</div>;
  }

  return (
    <div className="history-list">
      {history.map((entry) => (
        <button
          key={entry.textid}
          type="button"
          className={`history-row${entry.textid === activeTextid ? " on" : ""}`}
          onClick={() => workspace.openHistoryText(entry.textid)}
          title={entry.textid}
        >
          <span className={`history-cjk ${krClass(entry.textid)}`}>{(entry.title ?? entry.textid).slice(0, 2)}</span>
          <span className="history-main">
            <span className="history-title">{entry.title ?? entry.textid}</span>
            <span className="history-meta">
              <span className={krClass(entry.textid)}>{entry.textid}</span> · {pageLabel(entry)}
            </span>
          </span>
          {entry.pinned ? (
            <span className="history-pin" title="Pinned" aria-label="Pinned">
              ●
            </span>
          ) : null}
          <span className="history-date">{formatVisitedAt(entry.visitedAt)}</span>
        </button>
      ))}
    </div>
  );
}
