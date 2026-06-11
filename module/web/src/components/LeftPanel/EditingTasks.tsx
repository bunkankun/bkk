import { useEffect, useRef, useState } from "react";
import {
  acceptSyntacticFunctionWarning,
  lintSyntacticFunctions,
  subscribeCoreRecordSaved,
} from "../../api/client";
import type {
  CoreLintItem,
  SyntacticFunctionLintResponse,
} from "../../api/types";
import { workspace } from "../../state/useWorkspace";

const CUTOFF = 80;

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; report: SyntacticFunctionLintResponse }
  | { status: "error"; error: string };

export function EditingTasks() {
  const [state, setState] = useState<State>({ status: "idle" });
  const [showAll, setShowAll] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [acceptingKey, setAcceptingKey] = useState<string | null>(null);
  const hasResultRef = useRef(false);

  const refreshLint = async () => {
    try {
      const report = await lintSyntacticFunctions();
      setState({ status: "ok", report });
    } catch (err) {
      setState({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const runLint = async () => {
    setState({ status: "loading" });
    setShowAll(false);
    await refreshLint();
  };

  useEffect(() => {
    hasResultRef.current = state.status === "ok";
  }, [state.status]);

  useEffect(() => {
    return subscribeCoreRecordSaved(async (event) => {
      if (event.collection !== "syntactic-functions") return;
      if (!hasResultRef.current) return;
      setRefreshing(true);
      try {
        await refreshLint();
      } finally {
        setRefreshing(false);
      }
    });
  }, []);

  const acceptWarning = async (item: CoreLintItem) => {
    if (state.status !== "ok") return;
    const key = `${item.uuid}:${item.diagnostic.code}`;
    setAcceptingKey(key);
    // Optimistic: drop matching warnings on the same record.
    const previous = state.report;
    const remaining = previous.items.filter(
      (it) => !(it.uuid === item.uuid && it.diagnostic.code === item.diagnostic.code),
    );
    const dropped = previous.items.length - remaining.length;
    setState({
      status: "ok",
      report: {
        ...previous,
        items: remaining,
        warning_count: Math.max(0, previous.warning_count - dropped),
      },
    });
    try {
      await acceptSyntacticFunctionWarning(item.uuid, item.diagnostic.code);
    } catch (err) {
      // Roll back on failure.
      setState({ status: "ok", report: previous });
      const message = err instanceof Error ? err.message : String(err);
      window.alert(`Failed to record acceptance: ${message}`);
    } finally {
      setAcceptingKey(null);
    }
  };

  const openRecord = (item: CoreLintItem) => {
    workspace.openCoreRecord("syntactic-functions", item.uuid, { keepActivity: true });
  };

  return (
    <div className="lists-panel">
      <div className="lists-toolbar">
        <button
          type="button"
          onClick={runLint}
          disabled={state.status === "loading"}
        >
          {state.status === "loading"
            ? "Running…"
            : state.status === "ok"
              ? "Re-run lint"
              : "Lint syntactic functions"}
        </button>
        {refreshing && (
          <span style={{ fontSize: 11, color: "var(--t3)" }}>refreshing…</span>
        )}
      </div>
      <div
        className="lists-list"
        style={{ maxHeight: "none", flex: 1, minHeight: 0 }}
      >
        {state.status === "idle" && (
          <div className="empty">Click the button to run the linter.</div>
        )}
        {state.status === "loading" && <div className="empty">Running…</div>}
        {state.status === "error" && (
          <div className="empty">Failed: {state.error}</div>
        )}
        {state.status === "ok" && (
          <LintList
            report={state.report}
            showAll={showAll}
            onShowAll={() => setShowAll(true)}
            onShowTop={() => setShowAll(false)}
            onOpen={openRecord}
            onAccept={acceptWarning}
            acceptingKey={acceptingKey}
          />
        )}
      </div>
    </div>
  );
}

function LintList({
  report,
  showAll,
  onShowAll,
  onShowTop,
  onOpen,
  onAccept,
  acceptingKey,
}: {
  report: SyntacticFunctionLintResponse;
  showAll: boolean;
  onShowAll: () => void;
  onShowTop: () => void;
  onOpen: (item: CoreLintItem) => void;
  onAccept: (item: CoreLintItem) => void;
  acceptingKey: string | null;
}) {
  const total = report.items.length;
  const visible = showAll ? report.items : report.items.slice(0, CUTOFF);
  const hidden = total - visible.length;

  return (
    <>
      <div className="list-sub" style={{ padding: "6px 14px" }}>
        {report.error_count} error{report.error_count === 1 ? "" : "s"} ·{" "}
        {report.warning_count} warning{report.warning_count === 1 ? "" : "s"} ·{" "}
        {report.record_count} records
      </div>
      {total === 0 ? (
        <div className="empty">No diagnostics.</div>
      ) : (
        visible.map((item) => (
          <LintRow
            key={`${item.uuid}:${item.diagnostic.code}:${item.diagnostic.start ?? ""}`}
            item={item}
            onOpen={onOpen}
            onAccept={onAccept}
            accepting={acceptingKey === `${item.uuid}:${item.diagnostic.code}`}
          />
        ))
      )}
      {hidden > 0 && (
        <div style={{ padding: "8px 14px" }}>
          <button type="button" onClick={onShowAll}>
            Show all ({total})
          </button>
        </div>
      )}
      {showAll && total > CUTOFF && (
        <div style={{ padding: "8px 14px" }}>
          <button type="button" onClick={onShowTop}>
            Show top {CUTOFF}
          </button>
        </div>
      )}
    </>
  );
}

function LintRow({
  item,
  onOpen,
  onAccept,
  accepting,
}: {
  item: CoreLintItem;
  onOpen: (item: CoreLintItem) => void;
  onAccept: (item: CoreLintItem) => void;
  accepting: boolean;
}) {
  const isError = item.diagnostic.severity === "error";
  return (
    <div
      className="list-item"
      style={{ paddingLeft: 14, alignItems: "flex-start" }}
      onClick={() => onOpen(item)}
      title={item.diagnostic.message}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 12,
            color: "var(--t1)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          <span
            style={{
              display: "inline-block",
              marginRight: 6,
              padding: "0 4px",
              borderRadius: 3,
              fontSize: 10,
              fontWeight: 600,
              background: isError ? "var(--err, #c33)" : "var(--warn, #b80)",
              color: "#fff",
            }}
          >
            {isError ? "error" : "warn"}
          </span>
          <span style={{ opacity: 0.7, marginRight: 6 }}>{item.diagnostic.code}</span>
          <span>{item.label}</span>
        </div>
        <div className="list-sub">{item.diagnostic.message}</div>
      </div>
      {!isError && (
        <button
          type="button"
          title="Mark this warning as accepted on this record"
          disabled={accepting}
          onClick={(e) => {
            e.stopPropagation();
            onAccept(item);
          }}
          style={{
            marginLeft: 8,
            padding: "0 6px",
            fontSize: 14,
            lineHeight: "20px",
          }}
        >
          {accepting ? "…" : "✓"}
        </button>
      )}
    </div>
  );
}
