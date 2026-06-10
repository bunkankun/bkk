import { useCallback, useEffect, useRef, useState } from "react";
import {
  getContributions,
  patchContributionCuration,
  postComment,
  type CurationState,
} from "../../api/client";
import type { Contribution } from "../../api/types";
import { CommentIcon } from "../SenseUses";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { useLabelStore, type LabelStore } from "../Workspace/CoreRecordEditor";
import { AnnotationPayload } from "./AnnotationDisplay";

const REFRESH_MS = 15_000;
const SHORT_DID_HEAD = 12;
const SHORT_DID_TAIL = 4;

function shortDid(did: string): string {
  if (did.length <= SHORT_DID_HEAD + SHORT_DID_TAIL + 1) return did;
  return `${did.slice(0, SHORT_DID_HEAD)}…${did.slice(-SHORT_DID_TAIL)}`;
}

function PostIcon() {
  return (
    <svg className="core-target-action-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 20l18-8L3 4l4 8-4 8Z" />
    </svg>
  );
}

function relativeTime(timeUs: number, nowMs: number): string {
  if (!timeUs) return "";
  const deltaSec = Math.max(0, (nowMs - timeUs / 1000) / 1000);
  if (deltaSec < 60) return `${Math.floor(deltaSec)}s ago`;
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

type Thread = { root: Contribution; replies: Contribution[] };

function buildThreads(items: Contribution[]): Thread[] {
  const byUri = new Map(items.map((c) => [c.uri, c]));

  // Walk each item's parent chain up to its ultimate ancestor in the set.
  // This collapses reply-to-reply chains under the original root so a nested
  // comment still appears in the right thread.
  const ancestorOf = new Map<string, string>();
  for (const c of items) {
    let cur: Contribution = c;
    const seen = new Set<string>([cur.uri]);
    while (cur.parent?.uri && byUri.has(cur.parent.uri)) {
      const next = byUri.get(cur.parent.uri)!;
      if (seen.has(next.uri)) break;
      seen.add(next.uri);
      cur = next;
    }
    ancestorOf.set(c.uri, cur.uri);
  }

  const groups = new Map<string, Contribution[]>();
  for (const c of items) {
    const ancestor = ancestorOf.get(c.uri)!;
    const arr = groups.get(ancestor) ?? [];
    arr.push(c);
    groups.set(ancestor, arr);
  }

  const threads: Thread[] = [];
  for (const [rootUri, group] of groups) {
    const root = byUri.get(rootUri)!;
    const replies = group.filter((c) => c.uri !== rootUri);
    replies.sort((a, b) => a.time_us - b.time_us);
    threads.push({ root, replies });
  }

  threads.sort((a, b) => {
    const aLatest = Math.max(a.root.time_us, ...a.replies.map((r) => r.time_us));
    const bLatest = Math.max(b.root.time_us, ...b.replies.map((r) => r.time_us));
    return bLatest - aLatest;
  });

  return threads;
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

function ContribActions({
  c,
  isEditor,
  hasBluesky,
  onCurationChange,
  onToggleCompose,
}: {
  c: Contribution;
  isEditor: boolean;
  hasBluesky: boolean;
  onCurationChange: (uri: string, state: CurationState) => void;
  onToggleCompose: () => void;
}) {
  const [action, setAction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const state = (c.curation_state ?? "proposed") as CurationState;

  if (!hasBluesky) {
    if (c.kind === "comment") return null;
    return <span className={`contrib-state state-${state}`}>{state}</span>;
  }

  const showCurationDropdown = isEditor && c.kind !== "comment";

  const patch = async (next: CurationState) => {
    setBusy(true);
    setError(null);
    try {
      const res = await patchContributionCuration(c.uri, next);
      onCurationChange(c.uri, res.curation_state);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const onPost = () => {
    if (!action) return;
    if (action === "comment") {
      onToggleCompose();
      setAction("");
    } else {
      void patch(action as CurationState);
      setAction("");
    }
  };

  if (!showCurationDropdown) {
    return (
      <button
        type="button"
        className="contrib-action-icon"
        onClick={onToggleCompose}
        title="Comment"
        aria-label="Comment"
      >
        <CommentIcon />
      </button>
    );
  }

  return (
    <span className="contrib-actions-inline">
      <select
        className={`contrib-action-select ${action === "" ? `state-${state}` : ""}`}
        value={action}
        disabled={busy}
        onChange={(e) => setAction(e.currentTarget.value)}
        title={error ?? undefined}
      >
        <option value="">{state}</option>
        {state !== "accepted" && <option value="accepted">accept</option>}
        {state !== "rejected" && <option value="rejected">reject</option>}
        {state !== "proposed" && (
          <option value="edit" disabled>edit</option>
        )}
        <option value="comment">comment</option>
      </select>
      <button
        type="button"
        className="contrib-action-icon"
        disabled={!action || busy}
        onClick={onPost}
        title="Post selected action"
        aria-label="Post selected action"
      >
        <PostIcon />
      </button>
    </span>
  );
}

function CommentCompose({
  c,
  onClose,
}: {
  c: Contribution;
  onClose: () => void;
}) {
  const [body, setBody] = useState("");
  const [status, setStatus] = useState<"idle" | "busy" | "ok" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const text = body.trim();
    if (!text || status === "busy") return;
    setStatus("busy");
    setError(null);
    try {
      await postComment({
        text_id: c.text_id,
        body: text,
        lang: "en",
        parent: { uri: c.uri, cid: c.cid },
      });
      setStatus("ok");
      setBody("");
      setTimeout(onClose, 1500);
    } catch (exc) {
      setStatus("error");
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <div className="contrib-inline-compose">
      <textarea
        value={body}
        onChange={(e) => setBody(e.currentTarget.value)}
        placeholder="Add a comment…"
        rows={2}
        disabled={status === "busy"}
      />
      <div className="contrib-inline-compose-actions">
        <button
          type="button"
          className="contrib-action-icon"
          disabled={!body.trim() || status === "busy"}
          onClick={() => void submit()}
          title={status === "busy" ? "Posting…" : "Post comment"}
          aria-label="Post comment"
        >
          <PostIcon />
        </button>
        <button
          type="button"
          className="contrib-action-btn"
          onClick={onClose}
        >
          cancel
        </button>
      </div>
      {status === "ok" && <span className="ann-def">Posted!</span>}
      {status === "error" && error && (
        <span className="bsky-error">{error}</span>
      )}
    </div>
  );
}

function ContribCard({
  c,
  nowMs,
  store,
  isEditor,
  hasBluesky,
  depth,
  onCurationChange,
}: {
  c: Contribution;
  nowMs: number;
  store: LabelStore;
  isEditor: boolean;
  hasBluesky: boolean;
  depth: number;
  onCurationChange: (uri: string, state: CurationState) => void;
}) {
  const [showCompose, setShowCompose] = useState(false);
  const authorLabel = c.display_name ?? c.handle ?? shortDid(c.did);
  const authorTitle = [c.display_name, c.handle ? `@${c.handle}` : null]
    .filter(Boolean).join(" · ") || c.did;
  return (
    <div className={depth > 0 ? "ann ann-reply" : "ann"}>
      <div className="ann-head">
        <span className="ann-pron" title={authorTitle}>
          {c.avatar_url && (
            <img src={c.avatar_url} alt="" className="contrib-avatar" />
          )}
          <span className="ann-pron-label">{authorLabel}</span>
        </span>
        <span className="ann-offset">{c.kind} · {relativeTime(c.time_us, nowMs)}</span>
        <ContribActions
          c={c}
          isEditor={isEditor}
          hasBluesky={hasBluesky}
          onCurationChange={onCurationChange}
          onToggleCompose={() => setShowCompose((v) => !v)}
        />
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
      {showCompose && (
        <CommentCompose c={c} onClose={() => setShowCompose(false)} />
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
  const isEditor = useWorkspace((s) => s.auth.session?.user?.is_editor ?? false);
  const hasBluesky = useWorkspace((s) => s.blueskyStatus != null);

  const handleCurationChange = useCallback(
    (uri: string, state: CurationState) => {
      setItems((prev) =>
        prev.map((it) => (it.uri === uri ? { ...it, curation_state: state } : it)),
      );
    },
    [],
  );

  const threads = buildThreads(items);
  const cardProps = { nowMs, store: labelStore, isEditor, hasBluesky, onCurationChange: handleCurationChange };

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
      {threads.map(({ root, replies }) => (
        <div key={root.uri}>
          <ContribCard c={root} depth={0} {...cardProps} />
          {replies.map((r) => (
            <ContribCard key={r.uri} c={r} depth={1} {...cardProps} />
          ))}
        </div>
      ))}
      {truncated && (
        <div className="empty">Buffer full (500) — older posts evicted.</div>
      )}
    </div>
  );
}
