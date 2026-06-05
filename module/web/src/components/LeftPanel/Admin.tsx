import { useCallback, useEffect, useRef, useState } from "react";
import {
  getAdminInfo,
  getAdminJob,
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
import type { AdminInfoResponse, AdminJob } from "../../api/types";

type Tab = "dashboard" | "operations";

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
      </div>
      {tab === "dashboard" ? <Dashboard /> : <Operations />}
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
