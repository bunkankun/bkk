import { useEffect, useRef, useState } from "react";
import {
  acceptSyntacticFunctionWarning,
  getSyntacticFunctionUsage,
  lintSyntacticFunctions,
  subscribeCoreRecordSaved,
} from "../../api/client";
import type {
  CoreLintItem,
  SyntacticFunctionLintResponse,
  SyntacticFunctionUsageItem,
  SyntacticFunctionUsageResponse,
} from "../../api/types";
import { workspace } from "../../state/useWorkspace";

const CUTOFF = 80;

export function EditingTasks() {
  return (
    <div className="lists-panel" style={{ display: "flex", flexDirection: "column" }}>
      <SectionHeader>Lint syntactic functions</SectionHeader>
      <LintSyntacticFunctionsTask />
      <div style={{ borderTop: "1px solid var(--border, #ddd)", margin: "8px 0" }} />
      <SectionHeader>Unused / seldom-used syntactic functions</SectionHeader>
      <SyntacticFunctionUsageTask />
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: "6px 14px",
        fontSize: 11,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: 0.5,
        color: "var(--t2)",
      }}
    >
      {children}
    </div>
  );
}

// ---------- Lint task -------------------------------------------------------

type LintState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; report: SyntacticFunctionLintResponse }
  | { status: "error"; error: string };

function LintSyntacticFunctionsTask() {
  const [state, setState] = useState<LintState>({ status: "idle" });
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
    <div>
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
      <div className="lists-list">
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

// ---------- Usage task ------------------------------------------------------

type UsageState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; report: SyntacticFunctionUsageResponse }
  | { status: "error"; error: string };

function SyntacticFunctionUsageTask() {
  const [state, setState] = useState<UsageState>({ status: "idle" });
  const [showAll, setShowAll] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const hasResultRef = useRef(false);

  const refresh = async () => {
    try {
      const report = await getSyntacticFunctionUsage();
      setState({ status: "ok", report });
    } catch (err) {
      setState({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const run = async () => {
    setState({ status: "loading" });
    setShowAll(false);
    await refresh();
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
        await refresh();
      } finally {
        setRefreshing(false);
      }
    });
  }, []);

  const openRecord = (item: SyntacticFunctionUsageItem) => {
    workspace.openCoreRecord("syntactic-functions", item.uuid, { keepActivity: true });
  };

  return (
    <div>
      <div className="lists-toolbar">
        <button
          type="button"
          onClick={run}
          disabled={state.status === "loading"}
        >
          {state.status === "loading"
            ? "Running…"
            : state.status === "ok"
              ? "Re-run"
              : "Find unused syntactic functions"}
        </button>
        {refreshing && (
          <span style={{ fontSize: 11, color: "var(--t3)" }}>refreshing…</span>
        )}
      </div>
      <div className="lists-list">
        {state.status === "idle" && (
          <div className="empty">Click the button to scan usage.</div>
        )}
        {state.status === "loading" && <div className="empty">Running…</div>}
        {state.status === "error" && (
          <div className="empty">Failed: {state.error}</div>
        )}
        {state.status === "ok" && (
          <UsageList
            report={state.report}
            showAll={showAll}
            onShowAll={() => setShowAll(true)}
            onShowTop={() => setShowAll(false)}
            onOpen={openRecord}
          />
        )}
      </div>
    </div>
  );
}

function UsageList({
  report,
  showAll,
  onShowAll,
  onShowTop,
  onOpen,
}: {
  report: SyntacticFunctionUsageResponse;
  showAll: boolean;
  onShowAll: () => void;
  onShowTop: () => void;
  onOpen: (item: SyntacticFunctionUsageItem) => void;
}) {
  const total = report.items.length;
  const visible = showAll ? report.items : report.items.slice(0, CUTOFF);
  const hidden = total - visible.length;

  return (
    <>
      <div className="list-sub" style={{ padding: "6px 14px" }}>
        {report.record_count} record{report.record_count === 1 ? "" : "s"} ·{" "}
        {report.unused_count} unused
      </div>
      {total === 0 ? (
        <div className="empty">No syntactic-function records.</div>
      ) : (
        visible.map((item) => <UsageRow key={item.uuid} item={item} onOpen={onOpen} />)
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

function UsageRow({
  item,
  onOpen,
}: {
  item: SyntacticFunctionUsageItem;
  onOpen: (item: SyntacticFunctionUsageItem) => void;
}) {
  const unused = item.sense_count === 0;
  return (
    <div
      className="list-item"
      style={{ paddingLeft: 14, alignItems: "flex-start" }}
      onClick={() => onOpen(item)}
      title={item.label}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 12,
            color: "var(--t1)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            opacity: unused ? 1 : 0.85,
          }}
        >
          <span>{item.label || <em style={{ opacity: 0.6 }}>(no code)</em>}</span>
        </div>
        <div className="list-sub">
          <span style={{ marginRight: 10 }}>senses: {item.sense_count}</span>
          <span>attestations: {item.attestation_count}</span>
        </div>
      </div>
    </div>
  );
}
