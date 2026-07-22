import { useEffect, useState } from "react";
import { getBundleSearch, getManifest } from "../../api/client";
import type {
  BundleSearchResponse,
  Manifest,
  ManifestPart,
  SearchHit,
  TocEntry,
} from "../../api/types";
import { useWorkspace, workspace, type SectionFocus } from "../../state/useWorkspace";
import { parseMarkerId } from "../../lib/markers";

interface JuanItem {
  seq: number;
  label: string;
  marker_id?: string;
}

interface LocalTocItem {
  key: string;
  label: string;
  seq: number;
  bucket: "front" | "body" | "back";
  start: number;
  end: number;
  level: number;
  marker_id?: string;
}

type SearchLoad =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; response: BundleSearchResponse }
  | { status: "error"; error: string };

function isTocBucket(value: unknown): value is LocalTocItem["bucket"] {
  return value === "front" || value === "body" || value === "back";
}

function buildItems(manifest: Manifest): JuanItem[] {
  const parts: ManifestPart[] = manifest.assets?.parts ?? [];
  const tocBySeq = new Map<number, TocEntry>();
  for (const t of manifest.table_of_contents ?? []) {
    const seq = t.ref?.seq;
    if (typeof seq === "number" && !tocBySeq.has(seq)) tocBySeq.set(seq, t);
  }
  return parts.map((p) => {
    const t = tocBySeq.get(p.seq);
    const label = t?.label ?? `juan ${p.seq}`;
    const marker_id = t?.ref?.marker_id;
    return { seq: p.seq, label, marker_id };
  });
}

function tocLevel(entry: TocEntry): number {
  const level = entry.level;
  return typeof level === "number" && Number.isInteger(level) && level >= 1
    ? level
    : 1;
}

export function buildLocalItems(manifest: Manifest, seq: number | null): LocalTocItem[] {
  if (seq == null) return [];
  const items = (manifest.table_of_contents ?? [])
    .map((entry, index): LocalTocItem | null => {
      const ref = entry.ref;
      if (ref?.seq !== seq) return null;
      const span = ref.span;
      if (!span || span.length !== 3) return null;
      const [bucket, start, end] = span;
      if (!isTocBucket(bucket) || typeof start !== "number" || typeof end !== "number") {
        return null;
      }
      return {
        key: `${seq}:${bucket}:${start}:${end}:${index}`,
        label: entry.label ?? `${bucket} @${start}`,
        seq,
        bucket,
        start,
        end,
        level: tocLevel(entry),
        marker_id: ref.marker_id,
      };
    })
    .filter((item): item is LocalTocItem => item != null);
  return items.map((item, index) => {
    if (item.end > item.start) return item;
    const nextPeer = items
      .slice(index + 1)
      .find(
        (candidate) =>
          candidate.bucket === item.bucket &&
          candidate.level === item.level &&
          candidate.start > item.start,
      );
    if (!nextPeer) return item;
    return {
      ...item,
      key: `${item.seq}:${item.bucket}:${item.start}:${nextPeer.start}:${index}`,
      end: nextPeer.start,
    };
  });
}

export function Toc() {
  const activeTextid = useWorkspace((s) => s.activeTextid);
  const activeSeq = useWorkspace((s) => s.activeSeq);
  const sectionFocus = useWorkspace((s) => s.sectionFocus);
  const searchDistance = useWorkspace((s) => s.searchPrefs.searchDistance);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [searchLoad, setSearchLoad] = useState<SearchLoad>({ status: "idle" });
  const [tab, setTab] = useState<"juan" | "local" | "results">("juan");

  useEffect(() => {
    setQuery("");
    setDebouncedQuery("");
    setSearchLoad({ status: "idle" });
    setTab("juan");
    if (!activeTextid) {
      setManifest(null);
      return;
    }
    let cancelled = false;
    setManifest(null);
    setError(null);
    getManifest(activeTextid)
      .then((m) => {
        if (!cancelled) setManifest(m);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [activeTextid]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
    }, 180);
    return () => window.clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    if (!activeTextid || !debouncedQuery) {
      setSearchLoad({ status: "idle" });
      return;
    }
    const controller = new AbortController();
    setSearchLoad({ status: "loading" });
    setTab("results");
    getBundleSearch(activeTextid, debouncedQuery, {
      signal: controller.signal,
      searchDistance,
    })
      .then((response) => {
        if (!controller.signal.aborted) {
          setSearchLoad({ status: "ok", response });
        }
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setSearchLoad({ status: "error", error: String(e) });
      });
    return () => controller.abort();
  }, [activeTextid, debouncedQuery, searchDistance]);

  if (!activeTextid) {
    return (
      <div className="empty">Select a bundle from the catalog to see its juan list.</div>
    );
  }
  if (error) return <div className="empty">Failed to load manifest: {error}</div>;
  if (!manifest) return <div className="empty">Loading…</div>;

  const items = buildItems(manifest);
  const localItems = buildLocalItems(manifest, activeSeq);
  const title = manifest.metadata?.title ?? activeTextid;
  const searching = debouncedQuery.length > 0;
  const showResultsTab = query.trim().length > 0;
  const showLocalTab = activeSeq != null && localItems.length > 1;
  const showTabstrip = showResultsTab || showLocalTab;
  const resultsCount =
    searchLoad.status === "ok" ? searchLoad.response.hits.length : null;

  return (
    <div>
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid var(--bdr-l)",
          fontFamily: "var(--fc)",
          fontSize: 16,
          color: "var(--t1)",
        }}
        title={activeTextid}
      >
        {title}
      </div>
      <div className="toc-filter">
        <input
          type="text"
          placeholder="Search this text…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search within this text"
        />
        {query && (
          <button
            type="button"
            className="toc-filter-clear"
            onClick={() => {
              setQuery("");
              setTab("juan");
            }}
            title="Clear search"
          >
            ×
          </button>
        )}
      </div>
      {showTabstrip && (
        <div className="toc-tabstrip" role="tablist">
          <button
            type="button"
            role="tab"
            className={tab === "juan" ? "on" : ""}
            onClick={() => setTab("juan")}
            aria-selected={tab === "juan"}
          >
            巻
          </button>
          {showLocalTab && (
            <button
              type="button"
              role="tab"
              className={tab === "local" ? "on" : ""}
              onClick={() => setTab("local")}
              aria-selected={tab === "local"}
            >
              目次
            </button>
          )}
          <button
            type="button"
            role="tab"
            hidden={!showResultsTab}
            className={tab === "results" ? "on" : ""}
            onClick={() => setTab("results")}
            aria-selected={tab === "results"}
          >
            Results{searching ? ` (${resultsCount ?? "…"})` : ""}
          </button>
        </div>
      )}
      {tab === "results" && showResultsTab ? (
        <TocSearchResults load={searchLoad} />
      ) : tab === "local" && showLocalTab ? (
        <LocalTocList
          items={localItems}
          textid={activeTextid}
          active={sectionFocus}
        />
      ) : (
        <TocJuanList items={items} activeSeq={activeSeq} textid={activeTextid} />
      )}
    </div>
  );
}

function TocJuanList({
  items,
  activeSeq,
  textid,
}: {
  items: JuanItem[];
  activeSeq: number | null;
  textid: string;
}) {
  if (items.length === 0) return <div className="empty">No juan listed in manifest.</div>;
  return (
    <>
      {items.map((it) => {
        const parsed = it.marker_id ? parseMarkerId(it.marker_id) : null;
        const sub = parsed
          ? `${parsed.edition} · ${parsed.location}`
          : `seq ${it.seq}`;
        return (
          <div
            key={it.seq}
            className={`toc-item${it.seq === activeSeq ? " on" : ""}`}
            onClick={() => workspace.openJuan(textid, it.seq)}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="toc-cjk">{it.label}</div>
              <div className="toc-sub">{sub}</div>
            </div>
            <span className="toc-n">{it.seq}</span>
          </div>
        );
      })}
    </>
  );
}

function LocalTocList({
  items,
  textid,
  active,
}: {
  items: LocalTocItem[];
  textid: string;
  active: SectionFocus | null;
}) {
  return (
    <>
      {items.map((it) => {
        const disabled = it.end <= it.start;
        const parsed = it.marker_id ? parseMarkerId(it.marker_id) : null;
        const sub = parsed
          ? `${it.bucket} · ${parsed.edition} · ${parsed.location}`
          : `${it.bucket} @${it.start}–${it.end}`;
        const on =
          active?.textid === textid &&
          active.seq === it.seq &&
          active.bucket === it.bucket &&
          active.start === it.start &&
          active.end === it.end;
        return (
          <div
            key={it.key}
            className={`toc-item toc-local-item${on ? " on" : ""}${disabled ? " disabled" : ""}`}
            onClick={() => {
              if (disabled) return;
              workspace.setSectionFocus({
                textid,
                seq: it.seq,
                bucket: it.bucket,
                start: it.start,
                end: it.end,
                label: it.label,
                markerId: it.marker_id,
              });
            }}
            title={disabled ? "This TOC entry has an empty span" : undefined}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="toc-cjk">{it.label}</div>
              <div className="toc-sub">{sub}</div>
            </div>
          </div>
        );
      })}
    </>
  );
}

function TocSearchResults({ load }: { load: SearchLoad }) {
  if (load.status === "idle") return null;
  if (load.status === "loading") return <div className="empty">Searching…</div>;
  if (load.status === "error")
    return <div className="empty">Search failed: {load.error}</div>;
  const { response } = load;
  if (response.capped) {
    return (
      <div className="empty">
        Too many matches ({response.total}+) — refine the query.
      </div>
    );
  }
  if (response.hits.length === 0)
    return <div className="empty">No hits in this text.</div>;
  return (
    <>
      {response.hits.map((hit, idx) => (
        <TocHitRow key={`${hit.juan_seq}-${hit.bucket}-${hit.master_offset}-${idx}`} hit={hit} />
      ))}
    </>
  );
}

function TocHitRow({ hit }: { hit: SearchHit }) {
  const juanLabel = hit.toc_label ?? `juan ${hit.juan_seq}`;
  const bucketHint = hit.bucket !== "body" ? ` · ${hit.bucket}` : "";
  return (
    <div className="toc-hit" onClick={() => workspace.openHit(hit)}>
      <div className="toc-hit-head">
        {juanLabel}
        {bucketHint}
      </div>
      <span className="toc-hit-kwic">
        <span>{hit.left}</span>
        <strong>{hit.match}</strong>
        <span>{hit.right}</span>
      </span>
    </div>
  );
}
