import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAdminJob,
  getDuplication,
  postDuplicationAction,
} from "../../api/client";
import type {
  AdminJob,
  DuplicationAction,
  DuplicationDetailResponse,
  DuplicationSide,
  DuplicationSnippet,
} from "../../api/types";
import { krClass } from "../../lib/krClass";

type DetailState =
  | { status: "loading" }
  | { status: "ok"; data: DuplicationDetailResponse }
  | { status: "error"; error: string };

const WINDOW_CHARS = 250;

export function DuplicationViewer({ rowId }: { rowId: number }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getDuplication(rowId, { window: WINDOW_CHARS })
      .then((data) => {
        if (!cancelled) setState({ status: "ok", data });
      })
      .catch((e) => {
        if (!cancelled) setState({ status: "error", error: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [rowId]);

  if (state.status === "loading") {
    return <div className="empty">Loading duplication row {rowId}…</div>;
  }
  if (state.status === "error") {
    return (
      <div className="empty" style={{ color: "var(--kr2)" }}>
        Failed to load row {rowId}: {state.error}
      </div>
    );
  }
  const { row, sides } = state.data;
  const intra = row.intra_juan;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid var(--bd)",
          background: "var(--bg-pan)",
          fontSize: 12,
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <span style={{ color: "var(--t2)" }}>Row #{row.id}</span>
        <span style={{ color: "var(--t1)" }}>
          {intra ? "intra-juan" : "inter-juan"} · longest {row.longest_span} ·
          {" "}
          {row.cluster_count} cluster{row.cluster_count === 1 ? "" : "s"} · cov{" "}
          {(Math.max(row.coverage_a, row.coverage_b) * 100).toFixed(0)}%
        </span>
        {row.action && (
          <span
            style={{
              fontSize: 10,
              padding: "1px 6px",
              borderRadius: 2,
              color: "var(--grn)",
              border: "1px solid var(--grn)",
            }}
            title={`${row.action_actor ?? "?"} at ${row.action_at ?? "?"}`}
          >
            {row.action}
          </span>
        )}
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 0,
          overflow: "hidden",
        }}
      >
        <SidePanel label="A" side={sides.a} />
        <div style={{ borderLeft: "1px solid var(--bd)", overflow: "hidden" }}>
          <SidePanel label="B" side={sides.b} />
        </div>
      </div>

      <ActionBar rowId={row.id} intra={intra} />
    </div>
  );
}

function SidePanel({ label, side }: { label: string; side: DuplicationSide }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div
        style={{
          padding: "6px 12px",
          borderBottom: "1px solid var(--bd)",
          background: "var(--bg-pan)",
          fontSize: 12,
        }}
      >
        <span style={{ color: "var(--t2)", marginRight: 6 }}>{label}</span>
        <span className={krClass(side.textid)}>{side.textid}</span>
        <span style={{ color: "var(--t2)" }}>
          {" "}
          · 卷 {side.juan_seq} · {side.bucket} · [{side.longest[0]}–
          {side.longest[1]}] / {side.bucket_length}
        </span>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: "8px 12px" }}>
        <SnippetBlock
          label={`head ${side.head.offset}–${side.head.end}`}
          snippet={side.head}
          dupStart={side.longest[0]}
          dupEnd={side.longest[1]}
        />
        <div style={{ margin: "8px 0", color: "var(--t3)", fontSize: 10 }}>
          … ({side.longest[1] - side.longest[0]} chars between head and tail) …
        </div>
        <SnippetBlock
          label={`tail ${side.tail.offset}–${side.tail.end}`}
          snippet={side.tail}
          dupStart={side.longest[0]}
          dupEnd={side.longest[1]}
        />
      </div>
    </div>
  );
}

function SnippetBlock({
  label,
  snippet,
  dupStart,
  dupEnd,
}: {
  label: string;
  snippet: DuplicationSnippet;
  dupStart: number;
  dupEnd: number;
}) {
  const { text, offset, end, markers } = snippet;
  // Clip the duplicated span to this snippet's window, then convert to
  // local indices into `text`. Either side may be empty (e.g. the tail
  // snippet's "before" portion is the duplicate end region).
  const localStart = Math.max(0, Math.min(text.length, dupStart - offset));
  const localEnd = Math.max(localStart, Math.min(text.length, dupEnd - offset));
  const before = text.slice(0, localStart);
  const inside = text.slice(localStart, localEnd);
  const after = text.slice(localEnd);
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--t2)", marginBottom: 2 }}>
        {label} <span style={{ color: "var(--t3)" }}>· overlap [{dupStart}–{dupEnd})</span>
      </div>
      <div
        style={{
          fontFamily: "var(--font-cn, serif)",
          fontSize: 15,
          lineHeight: 1.7,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          color: "var(--t1)",
        }}
      >
        {text.length === 0 ? (
          <span style={{ color: "var(--t3)" }}>(empty)</span>
        ) : (
          <>
            <span style={{ color: "var(--t2)" }}>{before}</span>
            <span
              style={{
                background: "var(--amb-d)",
                color: "var(--t1)",
                borderTop: "1px solid var(--amb-b)",
                borderBottom: "1px solid var(--amb-b)",
              }}
            >
              {inside}
            </span>
            <span style={{ color: "var(--t2)" }}>{after}</span>
          </>
        )}
      </div>
      {markers.length > 0 && (
        <details style={{ marginTop: 4 }}>
          <summary style={{ fontSize: 10, color: "var(--t2)", cursor: "pointer" }}>
            {markers.length} marker{markers.length === 1 ? "" : "s"}{" "}
            <span style={{ color: "var(--t3)" }}>
              · snippet [{offset}–{end})
            </span>
          </summary>
          <ul
            style={{
              margin: "4px 0 0",
              padding: "0 0 0 16px",
              fontSize: 10,
              color: "var(--t2)",
            }}
          >
            {markers.map((m, i) => (
              <li key={i}>
                @{m.offset ?? "?"} · {m.type}
                {m.id ? ` · ${m.id}` : ""}
                {m.content ? ` · ${m.content}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function ActionBar({
  rowId,
  intra,
}: {
  rowId: number;
  intra: boolean;
}) {
  const actions: { key: DuplicationAction; label: string; danger?: boolean }[] = intra
    ? [
        { key: "keep", label: "Keep" },
        { key: "delete_span", label: "Delete duplicated span", danger: true },
      ]
    : [
        { key: "keep", label: "Keep" },
        { key: "delete_a_juan", label: "Delete A juan", danger: true },
        { key: "delete_a_span", label: "Delete A spans", danger: true },
        { key: "delete_b_juan", label: "Delete B juan", danger: true },
        { key: "delete_b_span", label: "Delete B spans", danger: true },
      ];
  return (
    <div
      style={{
        borderTop: "1px solid var(--bd)",
        background: "var(--bg-pan)",
        padding: "6px 12px",
        display: "flex",
        gap: 6,
        flexWrap: "wrap",
      }}
    >
      {actions.map((a) => (
        <ActionButton
          key={a.key}
          rowId={rowId}
          action={a.key}
          label={a.label}
          danger={a.danger}
        />
      ))}
    </div>
  );
}

function ActionButton({
  rowId,
  action,
  label,
  danger,
}: {
  rowId: number;
  action: DuplicationAction;
  label: string;
  danger?: boolean;
}) {
  const [job, setJob] = useState<AdminJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => stop, [stop]);

  const start = useCallback(() => {
    if (
      danger &&
      !window.confirm(
        `Apply ${action} to row ${rowId}? This will rewrite the bundle(s) on disk.`,
      )
    ) {
      return;
    }
    setError(null);
    setJob(null);
    postDuplicationAction(rowId, action)
      .then((j) => {
        setJob(j);
        if (j.status === "pending" || j.status === "running") {
          timerRef.current = window.setInterval(() => {
            getAdminJob(j.id)
              .then((next) => {
                setJob(next);
                if (next.status === "success" || next.status === "error") {
                  stop();
                }
              })
              .catch((e) => {
                setError(String(e));
                stop();
              });
          }, 1500);
        }
      })
      .catch((e) => setError(String(e)));
  }, [action, danger, rowId, stop]);

  const status = job?.status;
  const busy = status === "pending" || status === "running";
  const ok = status === "success";
  const errored = status === "error" || error != null;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <button
        type="button"
        onClick={start}
        disabled={busy}
        style={{
          padding: "4px 10px",
          fontSize: 12,
          background: "var(--bg-1)",
          color: danger ? "var(--kr2)" : "var(--t1)",
          border: `1px solid ${danger ? "var(--kr2)" : "var(--bd)"}`,
          borderRadius: 3,
          cursor: busy ? "wait" : "pointer",
        }}
        title={action}
      >
        {label}
      </button>
      {status && (
        <span
          style={{
            fontSize: 10,
            padding: "1px 6px",
            borderRadius: 2,
            color: ok ? "var(--grn)" : errored ? "var(--kr2)" : "var(--amb)",
            border: `1px solid ${
              ok ? "var(--grn)" : errored ? "var(--kr2)" : "var(--amb-b)"
            }`,
            background: ok || errored ? "transparent" : "var(--amb-d)",
          }}
          title={job?.error ?? ""}
        >
          {status}
        </span>
      )}
      {error && !job && (
        <span
          style={{
            fontSize: 10,
            padding: "1px 6px",
            borderRadius: 2,
            color: "var(--kr2)",
            border: "1px solid var(--kr2)",
          }}
          title={error}
        >
          failed
        </span>
      )}
    </div>
  );
}
