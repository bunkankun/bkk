import { useCallback, useEffect, useRef, useState } from "react";
import {
  getContributions,
  patchContributionCuration,
  type CurationState,
} from "../../api/client";
import type { Contribution } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { useLabelStore, type LabelStore } from "../Workspace/CoreRecordEditor";
import { AnnotationPayload } from "./AnnotationDisplay";

const CURATION_STATES: CurationState[] = [
  "proposed",
  "accepted",
  "rejected",
  "superseded",
];

const REFRESH_MS = 15_000;
const SHORT_DID_HEAD = 12;
const SHORT_DID_TAIL = 4;

function shortDid(did: string): string {
  if (did.length <= SHORT_DID_HEAD + SHORT_DID_TAIL + 1) return did;
  return `${did.slice(0, SHORT_DID_HEAD)}…${did.slice(-SHORT_DID_TAIL)}`;
}

function relativeTime(timeUs: number, nowMs: number): string {
  if (!timeUs) return "";
  const deltaSec = Math.max(0, (nowMs - timeUs / 1000) / 1000);
  if (deltaSec < 60) return `${Math.floor(deltaSec)}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

function LocationHeader({ c }: { c: Contribution }) {
  const juan = c.juan_seq ?? null;
  const canOpen = juan != null;
  const label = c.title ?? c.text_id;
  const parts: string[] = [label];
  if (juan != null) parts.push(`juan ${juan}`);
  else if (c.kind === "comment" && c.parent) parts.push("reply");
  const detail: string[] = [];
  if (c.edition) detail.push(c.edition);
  if (c.marker_id) {
    const offset = c.offset ?? 0;
    detail.push(`${c.marker_id}@${offset}`);
  }
  const onClick = () => {
    if (!canOpen) return;
    workspace.openContributionLocation({
      textid: c.text_id,
      seq: juan!,
      bucket: c.bucket ?? null,
      masterOffset: c.master_offset ?? null,
      length: c.length ?? null,
    });
  };
  return (
    <div className="ann-head">
      <button
        type="button"
        className="contrib-location"
        onClick={onClick}
        disabled={!canOpen}
        title={canOpen ? "Open in workspace" : undefined}
      >
        {parts.join(" · ")}
      </button>
      {detail.length > 0 && (
        <span className="ann-offset">{detail.join(" · ")}</span>
      )}
    </div>
  );
}

function CurationSelect({
  uri,
  current,
  onChange,
}: {
  uri: string;
  current: CurationState | null;
  onChange: (state: CurationState) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const value = current ?? "";

  const onSelect = async (next: CurationState) => {
    if (busy || next === current) return;
    setBusy(true);
    setError(null);
    try {
      const res = await patchContributionCuration(uri, next);
      onChange(res.curation_state);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="contrib-curation">
      <select
        value={value}
        disabled={busy}
        onChange={(ev) => void onSelect(ev.currentTarget.value as CurationState)}
        title={error ?? "Set curation state"}
      >
        {current == null && <option value="">curation…</option>}
        {CURATION_STATES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    </span>
  );
}

function ContribCard({
  c,
  nowMs,
  store,
  isEditor,
  onCurationChange,
}: {
  c: Contribution;
  nowMs: number;
  store: LabelStore;
  isEditor: boolean;
  onCurationChange: (uri: string, state: CurationState) => void;
}) {
  const authorLabel = c.display_name ?? c.handle ?? shortDid(c.did);
  const authorTitle = [c.display_name, c.handle ? `@${c.handle}` : null]
    .filter(Boolean).join(" · ") || c.did;
  return (
    <div className="ann">
      <div className="ann-head">
        <span className="ann-pron" title={authorTitle}>
          {c.avatar_url && (
            <img src={c.avatar_url} alt="" className="contrib-avatar" />
          )}
          <span className="ann-pron-label">{authorLabel}</span>
        </span>
        <span className="ann-offset">{c.kind} · {relativeTime(c.time_us, nowMs)}</span>
        {isEditor && (
          <CurationSelect
            uri={c.uri}
            current={(c.curation_state ?? null) as CurationState | null}
            onChange={(s) => onCurationChange(c.uri, s)}
          />
        )}
      </div>
      <LocationHeader c={c} />
      {c.kind === "annotation" && (
        <AnnotationPayload
          parts={{
            form: c.payload?.form,
            sense: c.payload?.sense,
            concept: c.payload?.concept,
            translation: c.payload?.translation,
          }}
          store={store}
        />
      )}
      {c.kind === "comment" && c.body && (
        <div className="ann-def">{c.body}</div>
      )}
      {c.kind === "translation" && c.text && (
        <div className="ann-tr">
          "{c.text}"
          {c.translation_id ? (
            <span className="ann-offset"> · {c.translation_id}</span>
          ) : null}
        </div>
      )}
    </div>
  );
}

export function ChatTab() {
  const [items, setItems] = useState<Contribution[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const res = await getContributions(50);
      setItems(res.items);
      setTruncated(res.truncated);
      setStatus("ok");
      setError(null);
    } catch (exc) {
      setStatus("error");
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    void load();
    const tick = () => {
      setNowMs(Date.now());
      if (!document.hidden) void load();
    };
    const id = window.setInterval(tick, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const labelStore = useLabelStore(new Map());
  const isEditor = useWorkspace(
    (s) => s.auth.session?.user?.is_editor ?? false,
  );

  const handleCurationChange = useCallback(
    (uri: string, state: CurationState) => {
      setItems((prev) =>
        prev.map((it) => (it.uri === uri ? { ...it, curation_state: state } : it)),
      );
    },
    [],
  );

  return (
    <div className="rc">
      <div className="rc-head">
        <span className="rc-title">Contributions ({items.length})</span>
        <button
          type="button"
          className="rc-refresh"
          onClick={() => void load()}
          disabled={status === "loading"}
        >
          Refresh
        </button>
      </div>
      {status === "loading" && items.length === 0 && (
        <div className="empty">Loading…</div>
      )}
      {status === "error" && (
        <div className="empty">Error: {error ?? "unknown"}</div>
      )}
      {status === "ok" && items.length === 0 && (
        <div className="empty">No contributions yet — start chatting!</div>
      )}
      {items.map((c) => (
        <ContribCard
          key={c.uri}
          c={c}
          nowMs={nowMs}
          store={labelStore}
          isEditor={isEditor}
          onCurationChange={handleCurationChange}
        />
      ))}
      {truncated && (
        <div className="empty">Buffer full (500) — older posts evicted.</div>
      )}
    </div>
  );
}
