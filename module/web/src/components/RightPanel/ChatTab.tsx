import { useCallback, useEffect, useRef, useState } from "react";
import { getContributions } from "../../api/client";
import type { Contribution } from "../../api/types";

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

function ContribBody({ c }: { c: Contribution }) {
  const { form, sense, translation, concept, metadata } = c.payload;
  if (form?.orth || form?.pron) {
    return (
      <div className="ann-head">
        {form.orth && <span className="ann-orth">{form.orth}</span>}
        {form.pron && <span className="ann-pron">{form.pron}</span>}
      </div>
    );
  }
  if (sense) {
    const def = sense.def_text ?? sense.def;
    if (sense.syn_func || sense.sem_feat || def) {
      return (
        <div className="ann-def">
          {sense.syn_func && <strong>{sense.syn_func}</strong>}
          {sense.sem_feat && <>{sense.syn_func && " "}<em>{sense.sem_feat}</em></>}
          {def && <>{(sense.syn_func || sense.sem_feat) && " "}{def}</>}
        </div>
      );
    }
    if (sense.id) return <div className="ann-def">sense {sense.id}</div>;
  }
  if (translation?.text) {
    return (
      <div className="ann-tr">
        "{translation.text}"
        {translation.src ? ` — ${translation.src}` : ""}
      </div>
    );
  }
  if (concept) return <div className="ann-concept">{concept}</div>;
  if (metadata && Object.keys(metadata).length > 0) {
    return <div className="ann-def">{JSON.stringify(metadata)}</div>;
  }
  return null;
}

function ContribCard({ c, nowMs }: { c: Contribution; nowMs: number }) {
  return (
    <div className="ann">
      <div className="ann-head">
        <span className="ann-pron">{shortDid(c.did)}</span>
        <span className="ann-offset">{relativeTime(c.time_us, nowMs)}</span>
      </div>
      <div className="ann-head">
        <span>{c.text_id} · {c.edition} · {c.marker_id}@{c.offset}</span>
      </div>
      <ContribBody c={c} />
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
      const res = await getContributions(200);
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
        <ContribCard key={c.uri} c={c} nowMs={nowMs} />
      ))}
      {truncated && (
        <div className="empty">Buffer full (500) — older posts evicted.</div>
      )}
    </div>
  );
}
