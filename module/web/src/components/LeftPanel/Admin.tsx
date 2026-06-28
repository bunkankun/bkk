import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAdminInfo,
  getAdminJob,
  getDuplications,
  getServerInfo,
  postAdminAnnotations,
  postAdminCatalog,
  postAdminCoreSync,
  postAdminIndex,
  postAdminIndexOne,
  postAdminRestart,
  postAdminTranslations,
  postAdminUpdate,
  postAdminValidate,
} from "../../api/client";
import type {
  AdminInfoResponse,
  AdminJob,
  DuplicationListResponse,
  DuplicationRowSummary,
} from "../../api/types";
import { krClass } from "../../lib/krClass";
import { useWorkspace, workspace, type PaneNode } from "../../state/useWorkspace";

type Tab = "dashboard" | "operations" | "duplications";

export function Admin() {
  const [tab, setTab] = useState<Tab>("dashboard");
  return (
    <div>
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--bd)",
          background: "var(--bg-pan)",
          position: "sticky",
          top: 0,
          zIndex: 2,
        }}
      >
        <TabButton active={tab === "dashboard"} onClick={() => setTab("dashboard")}>
          Dashboard
        </TabButton>
        <TabButton active={tab === "operations"} onClick={() => setTab("operations")}>
          Operations
        </TabButton>
        <TabButton active={tab === "duplications"} onClick={() => setTab("duplications")}>
          Duplications
        </TabButton>
      </div>
      {tab === "dashboard" ? (
        <Dashboard />
      ) : tab === "operations" ? (
        <Operations />
      ) : (
        <Duplications />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        flex: 1,
        padding: "6px 8px",
        fontSize: 12,
        background: active ? "var(--bg-1)" : "transparent",
        color: active ? "var(--t1)" : "var(--t2)",
        border: "none",
        borderBottom: active ? "2px solid var(--blu)" : "2px solid transparent",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

// ---------- Dashboard ----------

type InfoState =
  | { status: "loading" }
  | { status: "ok"; info: AdminInfoResponse }
  | { status: "error"; error: string };

function Dashboard() {
  const [state, setState] = useState<InfoState>({ status: "loading" });

  const refresh = useCallback(() => {
    setState({ status: "loading" });
    getAdminInfo()
      .then((info) => setState({ status: "ok", info }))
      .catch((e) => setState({ status: "error", error: String(e) }));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (state.status === "loading") return <div className="empty">Loading…</div>;
  if (state.status === "error")
    return <div className="empty">Failed: {state.error}</div>;

  const info = state.info;
  const stale = info.index.per_bundle_indices_stale ?? 0;

  return (
    <div style={{ padding: "8px 10px", fontSize: 12 }}>
      {stale > 0 && (
        <div
          style={{
            background: "var(--amb-d)",
            color: "var(--amb)",
            border: "1px solid var(--amb-b)",
            padding: "6px 8px",
            borderRadius: 3,
            marginBottom: 8,
          }}
        >
          {stale} of {info.index.per_bundle_indices_checked ?? 0} per-bundle indices stale.
        </div>
      )}

      <Section label="Server">
        <Row k="version" v={info.server_version} />
      </Section>

      <Section label="Corpus">
        <Row k="path" v={info.corpus.path} mono />
        <Row k="bundles" v={String(info.corpus.bundle_count)} />
        {Object.entries(info.corpus.by_section).map(([sec, n]) => (
          <Row key={sec} k={`  ${sec}`} v={String(n)} />
        ))}
      </Section>

      <Section label="Index">
        <Row k="path" v={info.index.path} mono />
        {info.index.built ? (
          <>
            <Row
              k="schema"
              v={`v${info.index.schema_version} (current v${info.index.schema_current})`}
              badge={info.index.schema_ok ? "ok" : "stale"}
              ok={info.index.schema_ok}
            />
            {info.index.counts &&
              Object.entries(info.index.counts).map(([k, n]) =>
                n == null ? null : <Row key={k} k={`  ${k}`} v={String(n)} />,
              )}
            {info.index.per_bundle_indices_checked != null && (
              <Row
                k="per-bundle"
                v={`${stale} of ${info.index.per_bundle_indices_checked} stale`}
                ok={stale === 0}
                badge={stale === 0 ? "ok" : "stale"}
              />
            )}
          </>
        ) : (
          <Row k="status" v="not built" ok={false} badge="missing" />
        )}
      </Section>

      <Section label="Catalog">
        <Row k="path" v={info.catalog.path} mono />
        {info.catalog.built ? (
          <Row
            k="schema"
            v={`v${info.catalog.schema_version} (current v${info.catalog.schema_current})`}
            badge={info.catalog.schema_ok ? "ok" : "stale"}
            ok={info.catalog.schema_ok}
          />
        ) : (
          <Row k="status" v="not built" ok={false} badge="missing" />
        )}
      </Section>

      {info.core && (
        <Section label="Core">
          <Row
            k="path"
            v={info.core.path}
            mono
            badge={info.core.built ? "ok" : "missing"}
            ok={info.core.built}
          />
          {info.core.root && <Row k="root" v={info.core.root} mono />}
          <Row
            k="editing"
            v={info.core.editing_enabled ? "enabled" : "disabled"}
            badge={info.core.editing_enabled ? "ok" : "off"}
            ok={info.core.editing_enabled}
          />
          <Row
            k="upstream"
            v={info.core.upstream_repo ?? "(not set)"}
            mono={!!info.core.upstream_repo}
          />
          <Row k="pr_base" v={info.core.pr_base ?? "(default)"} />
          {!info.core.editing_enabled && (
            <div
              style={{
                marginTop: 4,
                padding: "4px 6px",
                fontSize: 11,
                color: "var(--amb)",
                background: "var(--amb-d)",
                border: "1px solid var(--amb-b)",
                borderRadius: 2,
              }}
            >
              Inline editing returns 503 until <code>core.upstream_repo</code> is
              set in <code>.bkkrc</code> (or <code>BKK_CORE_UPSTREAM_REPO</code>)
              as <code>owner/repo</code> and the server is restarted.
            </div>
          )}
        </Section>
      )}

      {info.source && (
        <Section label="Source">
          <Row
            k="path"
            v={info.source.path}
            mono
            badge={info.source.is_git ? "git" : "not git"}
            ok={info.source.is_git}
          />
          <Row k="branch" v={info.source.branch} />
        </Section>
      )}

      {info.annotations && (
        <Section label="Annotations">
          <Row
            k="path"
            v={info.annotations.path}
            mono
            badge={info.annotations.built ? "ok" : "missing"}
            ok={info.annotations.built}
          />
        </Section>
      )}

      <Section label="Config">
        {info.config.files.length === 0 ? (
          <Row k=".bkkrc" v="(none found)" ok={false} badge="missing" />
        ) : (
          info.config.files.map((f, i) => (
            <Row
              key={f}
              k={i === 0 ? ".bkkrc" : ""}
              v={f}
              mono
              badge={i === info.config.files.length - 1 ? "highest" : undefined}
              ok={i === info.config.files.length - 1}
            />
          ))
        )}
      </Section>

      <div style={{ marginTop: 10 }}>
        <button
          type="button"
          onClick={refresh}
          style={{
            padding: "4px 10px",
            fontSize: 12,
            background: "var(--bg-1)",
            color: "var(--t1)",
            border: "1px solid var(--bd)",
            borderRadius: 3,
            cursor: "pointer",
          }}
        >
          Refresh
        </button>
      </div>
    </div>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div
        style={{
          fontSize: 11,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          color: "var(--t2)",
          marginBottom: 2,
        }}
      >
        {label}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Row({
  k,
  v,
  mono,
  badge,
  ok,
}: {
  k: string;
  v: string;
  mono?: boolean;
  badge?: string;
  ok?: boolean;
}) {
  return (
    <div style={{ display: "flex", gap: 6, padding: "1px 0" }}>
      <span style={{ width: 80, color: "var(--t2)", flexShrink: 0 }}>{k}</span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          color: "var(--t1)",
          fontFamily: mono ? "var(--font-mono, monospace)" : undefined,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
        title={v}
      >
        {v}
      </span>
      {badge && (
        <span
          style={{
            fontSize: 10,
            padding: "0 5px",
            borderRadius: 2,
            background: ok ? "transparent" : "var(--amb-d)",
            color: ok ? "var(--grn)" : "var(--amb)",
            border: `1px solid ${ok ? "var(--grn)" : "var(--amb-b)"}`,
          }}
        >
          {badge}
        </span>
      )}
    </div>
  );
}

// ---------- Operations ----------

function Operations() {
  const [textid, setTextid] = useState("");
  const trimmed = textid.trim();
  return (
    <div style={{ padding: "8px 10px", fontSize: 12 }}>
      <OpGroup label="Corpus indexes">
        <JobRow label="Rebuild corpus index" run={postAdminIndex} />
        <JobRow label="Rebuild catalog" run={postAdminCatalog} />
        <JobRow label="Rebuild translations" run={postAdminTranslations} />
        <JobRow label="Rebuild annotations" run={postAdminAnnotations} />
      </OpGroup>

      <OpGroup label="Per-bundle">
        <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
          <input
            type="text"
            placeholder="textid"
            value={textid}
            onChange={(e) => setTextid(e.target.value)}
            style={{
              flex: 1,
              padding: "3px 6px",
              fontSize: 12,
              background: "var(--bg-1)",
              color: "var(--t1)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
            }}
          />
        </div>
        <JobRow
          label="Reindex"
          disabled={!trimmed}
          run={() => postAdminIndexOne(trimmed)}
        />
        <JobRow
          label="Validate"
          disabled={!trimmed}
          run={() => postAdminValidate(trimmed)}
        />
      </OpGroup>

      <OpGroup label="bkk-core">
        <JobRow label="Sync from upstream" run={postAdminCoreSync} />
      </OpGroup>

      <OpGroup label="Server">
        <JobRow
          label="Update (git pull + pip install)"
          run={postAdminUpdate}
          confirm="Pull latest source and reinstall the bkk package? This can take a minute. The server is NOT restarted automatically."
        />
        <RestartButton />
      </OpGroup>
    </div>
  );
}

function RestartButton() {
  const [phase, setPhase] = useState<
    "idle" | "stopping" | "waiting" | "online" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);

  const start = useCallback(() => {
    if (!window.confirm(
      "Restart the server? Active sessions stay logged in, but any running admin jobs will be killed and in-flight requests will fail.",
    )) {
      return;
    }
    setError(null);
    setPhase("stopping");
    postAdminRestart()
      .then(() => {
        setPhase("waiting");
        const deadline = Date.now() + 60_000;
        const tick = () => {
          getServerInfo()
            .then(() => setPhase("online"))
            .catch(() => {
              if (Date.now() > deadline) {
                setPhase("error");
                setError("server did not return within 60s");
              } else {
                window.setTimeout(tick, 1500);
              }
            });
        };
        window.setTimeout(tick, 2000);
      })
      .catch((e) => {
        setPhase("error");
        setError(String(e));
      });
  }, []);

  const badge =
    phase === "stopping"
      ? "stopping"
      : phase === "waiting"
        ? "waiting"
        : phase === "online"
          ? "online"
          : phase === "error"
            ? "error"
            : null;
  const ok = phase === "online";
  const pending = phase === "stopping" || phase === "waiting";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0" }}>
      <button
        type="button"
        onClick={start}
        disabled={pending}
        style={{
          flex: 1,
          textAlign: "left",
          padding: "3px 8px",
          fontSize: 12,
          background: "var(--bg-1)",
          color: "var(--t1)",
          border: "1px solid var(--bd)",
          borderRadius: 3,
          cursor: pending ? "wait" : "pointer",
        }}
      >
        Restart server
      </button>
      {badge && (
        <span
          style={{
            fontSize: 10,
            padding: "1px 6px",
            borderRadius: 2,
            background:
              phase === "error"
                ? "transparent"
                : ok
                  ? "transparent"
                  : "var(--amb-d)",
            color: phase === "error" ? "var(--kr2)" : ok ? "var(--grn)" : "var(--amb)",
            border: `1px solid ${
              phase === "error" ? "var(--kr2)" : ok ? "var(--grn)" : "var(--amb-b)"
            }`,
          }}
          title={error ?? ""}
        >
          {badge}
        </span>
      )}
    </div>
  );
}

function OpGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          fontSize: 11,
          textTransform: "uppercase",
          letterSpacing: 0.5,
          color: "var(--t2)",
          marginBottom: 4,
        }}
      >
        {label}
      </div>
      <div>{children}</div>
    </div>
  );
}

function JobRow({
  label,
  run,
  disabled,
  confirm,
}: {
  label: string;
  run: () => Promise<AdminJob>;
  disabled?: boolean;
  confirm?: string;
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
    if (disabled) return;
    if (confirm && !window.confirm(confirm)) return;
    setError(null);
    setJob(null);
    run()
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
  }, [run, disabled, confirm, stop]);

  const status = job?.status;
  const badgeFg =
    status === "success"
      ? "var(--grn)"
      : status === "error"
        ? "var(--kr2)"
        : "var(--amb)";
  const badgeBg =
    status === "success"
      ? "transparent"
      : status === "error"
        ? "transparent"
        : "var(--amb-d)";
  const badgeBd =
    status === "success"
      ? "var(--grn)"
      : status === "error"
        ? "var(--kr2)"
        : "var(--amb-b)";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0" }}>
      <button
        type="button"
        onClick={start}
        disabled={disabled || status === "running" || status === "pending"}
        style={{
          flex: 1,
          textAlign: "left",
          padding: "3px 8px",
          fontSize: 12,
          background: "var(--bg-1)",
          color: disabled ? "var(--t2)" : "var(--t1)",
          border: "1px solid var(--bd)",
          borderRadius: 3,
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        {label}
      </button>
      {status && (
        <span
          style={{
            fontSize: 10,
            padding: "1px 6px",
            borderRadius: 2,
            background: badgeBg,
            color: badgeFg,
            border: `1px solid ${badgeBd}`,
          }}
          title={job?.error ?? (typeof job?.result === "string" ? job.result : "")}
        >
          {status}
        </span>
      )}
      {error && (
        <span
          style={{
            fontSize: 10,
            padding: "1px 6px",
            borderRadius: 2,
            background: "transparent",
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

// ---------- Duplications ----------

type DupFilter = "pending" | "all" | "done";
const DUP_PAGE_SIZE = 100;

type DupListState =
  | { status: "loading" }
  | { status: "ok"; data: DuplicationListResponse }
  | { status: "error"; error: string };

function Duplications() {
  const [filter, setFilter] = useState<DupFilter>("pending");
  const [offset, setOffset] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [list, setList] = useState<DupListState>({ status: "loading" });
  const focusedPaneId = useWorkspace((s) => s.focusedPaneId);
  const pane = useWorkspace((s) => s.pane);

  // Read the active tab in the focused pane (best-effort) so we can
  // highlight the row that the workspace is currently showing.
  const activeRowId = (() => {
    const stack: PaneNode[] = [pane];
    while (stack.length) {
      const node = stack.shift()!;
      if (node.kind === "leaf") {
        if (focusedPaneId && node.id !== focusedPaneId) continue;
        const tab = node.tabs.find((t) => t.id === node.activeTabId);
        if (tab?.type === "duplication") return tab.rowId;
      } else {
        stack.push(...node.children);
      }
    }
    return null;
  })();

  useEffect(() => {
    let cancelled = false;
    setList({ status: "loading" });
    getDuplications({ limit: DUP_PAGE_SIZE, offset, filter })
      .then((data) => {
        if (!cancelled) setList({ status: "ok", data });
      })
      .catch((e) => {
        if (!cancelled) setList({ status: "error", error: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [filter, offset, reloadToken]);

  const onFilter = (f: DupFilter) => {
    setFilter(f);
    setOffset(0);
  };

  return (
    <div style={{ padding: "8px 10px", fontSize: 12 }}>
      <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {(["pending", "all", "done"] as DupFilter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => onFilter(f)}
            style={{
              flex: 1,
              padding: "3px 6px",
              fontSize: 11,
              background: filter === f ? "var(--bg-1)" : "transparent",
              color: filter === f ? "var(--t1)" : "var(--t2)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
              cursor: "pointer",
              textTransform: "capitalize",
            }}
          >
            {f}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setReloadToken((n) => n + 1)}
          title="Refresh"
          style={{
            padding: "3px 8px",
            fontSize: 11,
            background: "transparent",
            color: "var(--t2)",
            border: "1px solid var(--bd)",
            borderRadius: 3,
            cursor: "pointer",
          }}
        >
          ↻
        </button>
      </div>

      {list.status === "loading" && <div className="empty">Loading…</div>}
      {list.status === "error" && (
        <div className="empty" style={{ color: "var(--kr2)" }}>
          {list.error}
        </div>
      )}
      {list.status === "ok" && (
        <>
          <div style={{ fontSize: 11, color: "var(--t2)", marginBottom: 4 }}>
            {list.data.total} row{list.data.total === 1 ? "" : "s"} ·{" "}
            showing {offset + 1}–{Math.min(offset + list.data.returned, list.data.total)}
          </div>
          <div>
            {list.data.rows.map((row) => (
              <DupListRow
                key={row.id}
                row={row}
                active={row.id === activeRowId}
                onClick={() => workspace.openDuplication(row.id)}
              />
            ))}
            {list.data.returned === 0 && (
              <div className="empty">No rows.</div>
            )}
          </div>
          <DupPager
            total={list.data.total}
            offset={offset}
            limit={DUP_PAGE_SIZE}
            onOffset={setOffset}
          />
        </>
      )}
    </div>
  );
}

function DupListRow({
  row,
  active,
  onClick,
}: {
  row: DuplicationRowSummary;
  active: boolean;
  onClick: () => void;
}) {
  const cov = Math.max(row.coverage_a, row.coverage_b);
  const intra = row.intra_juan;
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "4px 6px",
        marginBottom: 2,
        fontSize: 11,
        background: active ? "var(--bg-1)" : "transparent",
        color: "var(--t1)",
        border: `1px solid ${active ? "var(--blu)" : "var(--bd)"}`,
        borderRadius: 3,
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 4 }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {intra ? (
            <>
              <span className={krClass(row.textid_a)}>{row.textid_a}</span>
              :{row.juan_seq_a}/{row.bucket_a}{" "}
              <span style={{ color: "var(--t3)" }}>(intra)</span>
            </>
          ) : (
            <>
              <span className={krClass(row.textid_a)}>{row.textid_a}</span>
              :{row.juan_seq_a} ↔{" "}
              <span className={krClass(row.textid_b)}>{row.textid_b}</span>
              :{row.juan_seq_b}
            </>
          )}
        </span>
        {row.action && (
          <span
            style={{
              fontSize: 10,
              padding: "0 4px",
              borderRadius: 2,
              color: row.action === "keep" ? "var(--t2)" : "var(--grn)",
              border: `1px solid ${row.action === "keep" ? "var(--bd)" : "var(--grn)"}`,
            }}
            title={`${row.action} by ${row.action_actor ?? "?"} at ${row.action_at ?? "?"}`}
          >
            {row.action}
          </span>
        )}
      </div>
      <div style={{ marginTop: 2, fontSize: 10, color: "var(--t2)" }}>
        longest {row.longest_span} · {row.cluster_count} cluster
        {row.cluster_count === 1 ? "" : "s"} · cov {(cov * 100).toFixed(0)}%
      </div>
    </button>
  );
}

function DupPager({
  total,
  offset,
  limit,
  onOffset,
}: {
  total: number;
  offset: number;
  limit: number;
  onOffset: (o: number) => void;
}) {
  if (total <= limit) return null;
  const canPrev = offset > 0;
  const canNext = offset + limit < total;
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: 4,
        marginTop: 8,
      }}
    >
      <button
        type="button"
        disabled={!canPrev}
        onClick={() => onOffset(Math.max(0, offset - limit))}
        style={{
          padding: "3px 8px",
          fontSize: 11,
          background: "var(--bg-1)",
          color: canPrev ? "var(--t1)" : "var(--t3)",
          border: "1px solid var(--bd)",
          borderRadius: 3,
          cursor: canPrev ? "pointer" : "not-allowed",
        }}
      >
        ← Prev
      </button>
      <button
        type="button"
        disabled={!canNext}
        onClick={() => onOffset(offset + limit)}
        style={{
          padding: "3px 8px",
          fontSize: 11,
          background: "var(--bg-1)",
          color: canNext ? "var(--t1)" : "var(--t3)",
          border: "1px solid var(--bd)",
          borderRadius: 3,
          cursor: canNext ? "pointer" : "not-allowed",
        }}
      >
        Next →
      </button>
    </div>
  );
}
