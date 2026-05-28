import { useCallback, useEffect, useState, type MouseEvent } from "react";
import { getCatalog, getCategories, getTimeline } from "../../api/client";
import type {
  CatalogMatch,
  CategoriesResponse,
  CategoryNode,
  TimelineBucket,
  TimelineResponse,
} from "../../api/types";
import { workspace, useWorkspace } from "../../state/useWorkspace";
import { listPathFromName } from "../../lib/textLists";

type SubLoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; matches: CatalogMatch[] }
  | { status: "error"; error: string };

type CatalogMode = "categories" | "timeline";

export function Catalog({ mode }: { mode: CatalogMode }) {
  const [cats, setCats] = useState<CategoriesResponse | null>(null);
  const [catsError, setCatsError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [debouncedFilter, setDebouncedFilter] = useState("");
  const [openTops, setOpenTops] = useState<Set<string>>(() => new Set());
  const [openSubs, setOpenSubs] = useState<Set<string>>(() => new Set());
  const [openBuckets, setOpenBuckets] = useState<Set<string>>(() => new Set());
  const [subLoads, setSubLoads] = useState<Record<string, SubLoadState>>({});
  const [bucketLoads, setBucketLoads] = useState<Record<string, SubLoadState>>({});
  const [searchLoad, setSearchLoad] = useState<SubLoadState>({ status: "idle" });
  const activeTextid = useWorkspace((s) => s.activeTextid);

  useEffect(() => {
    let cancelled = false;
    getCategories()
      .then((d) => {
        if (!cancelled) setCats(d);
      })
      .catch((e) => {
        if (!cancelled) setCatsError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedFilter(filter.trim());
    }, 180);
    return () => window.clearTimeout(handle);
  }, [filter]);

  useEffect(() => {
    if (!debouncedFilter) {
      setSearchLoad({ status: "idle" });
      return;
    }
    const controller = new AbortController();
    setSearchLoad({ status: "loading" });
    getCatalog({ q: debouncedFilter, limit: 100 })
      .then((r) => {
        if (!controller.signal.aborted) {
          setSearchLoad({ status: "ok", matches: r.matches });
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setSearchLoad({ status: "error", error: String(e) });
        }
      });
    return () => controller.abort();
  }, [debouncedFilter]);

  useEffect(() => {
    if (mode !== "timeline" || timeline || timelineError) return;
    let cancelled = false;
    getTimeline()
      .then((d) => {
        if (!cancelled) setTimeline(d);
      })
      .catch((e) => {
        if (!cancelled) setTimelineError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [mode, timeline, timelineError]);

  const toggleTop = useCallback((code: string) => {
    setOpenTops((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  const toggleSub = useCallback((code: string) => {
    setOpenSubs((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
    const shouldFetch =
      subLoads[code]?.status !== "ok" && subLoads[code]?.status !== "loading";
    setSubLoads((prev) => {
      if (prev[code]?.status === "ok" || prev[code]?.status === "loading") return prev;
      return { ...prev, [code]: { status: "loading" } };
    });
    if (!shouldFetch) return;
    getCatalog({
      limit: 200,
      filters: { "tags.kr-categories": [code] },
    })
      .then((r) => {
        setSubLoads((prev) => ({
          ...prev,
          [code]: { status: "ok", matches: r.matches },
        }));
      })
      .catch((e) => {
        setSubLoads((prev) => ({
          ...prev,
          [code]: { status: "error", error: String(e) },
        }));
      });
  }, [subLoads]);

  const toggleBucket = useCallback((bucket: TimelineBucket) => {
    setOpenBuckets((prev) => {
      const next = new Set(prev);
      if (next.has(bucket.key)) next.delete(bucket.key);
      else next.add(bucket.key);
      return next;
    });
    const shouldFetch =
      bucketLoads[bucket.key]?.status !== "ok" &&
      bucketLoads[bucket.key]?.status !== "loading";
    setBucketLoads((prev) => {
      if (
        prev[bucket.key]?.status === "ok" ||
        prev[bucket.key]?.status === "loading"
      ) return prev;
      return { ...prev, [bucket.key]: { status: "loading" } };
    });
    if (!shouldFetch) return;
    getCatalog({ limit: 200, century: bucket.key })
      .then((r) => {
        setBucketLoads((prev) => ({
          ...prev,
          [bucket.key]: { status: "ok", matches: r.matches },
        }));
      })
      .catch((e) => {
        setBucketLoads((prev) => ({
          ...prev,
          [bucket.key]: { status: "error", error: String(e) },
        }));
      });
  }, [bucketLoads]);

  if (catsError) return <div className="empty">Failed to load categories: {catsError}</div>;
  if (!cats) return <div className="empty">Loading categories…</div>;

  const searching = debouncedFilter.length > 0;

  return (
    <div>
      <div className="cat-filter">
        <input
          type="text"
          placeholder="Search title, pinyin, English, ID…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Search catalog"
        />
        {filter && (
          <button
            type="button"
            className="cat-filter-clear"
            onClick={() => setFilter("")}
            title="Clear filter"
          >
            ×
          </button>
        )}
      </div>
      {searching ? (
        <CatalogMatchList
          state={searchLoad}
          activeTextid={activeTextid}
          empty="No catalog results."
        />
      ) : mode === "timeline" ? (
        <TimelineView
          timeline={timeline}
          error={timelineError}
          openBuckets={openBuckets}
          bucketLoads={bucketLoads}
          activeTextid={activeTextid}
          onToggleBucket={toggleBucket}
        />
      ) : (
        cats.categories.map((top) => (
          <CategoryRow
            key={top.code}
            node={top}
            depth={0}
            isOpen={openTops.has(top.code)}
            openSubs={openSubs}
            subLoads={subLoads}
            activeTextid={activeTextid}
            onToggleTop={toggleTop}
            onToggleSub={toggleSub}
          />
        ))
      )}
    </div>
  );
}

interface CategoryRowProps {
  node: CategoryNode;
  depth: number;
  isOpen: boolean;
  openSubs: Set<string>;
  subLoads: Record<string, SubLoadState>;
  activeTextid: string | null;
  onToggleTop: (code: string) => void;
  onToggleSub: (code: string) => void;
}

function CategoryRow(p: CategoryRowProps) {
  const { node, depth, isOpen } = p;
  const empty = node.bundle_count === 0;
  return (
    <div>
      <div
        className={`cat-top${empty ? " cat-empty" : ""}`}
        onClick={() => !empty && p.onToggleTop(node.code)}
        title={node.label}
      >
        <span className="cat-caret">{empty ? "·" : isOpen ? "▾" : "▸"}</span>
        <span className="cat-zh">{node.zh}</span>
        <span className="cat-code">{node.code}</span>
        <span className="cat-count">{node.bundle_count}</span>
      </div>
      {isOpen &&
        node.subcategories.map((sub) => (
          <CategoryNodeRow
            key={sub.code}
            node={sub}
            depth={depth + 1}
            isOpen={p.openSubs.has(sub.code)}
            openSubs={p.openSubs}
            subLoads={p.subLoads}
            activeTextid={p.activeTextid}
            onToggle={p.onToggleSub}
          />
        ))}
    </div>
  );
}

interface CategoryNodeRowProps {
  node: CategoryNode;
  depth: number;
  isOpen: boolean;
  openSubs: Set<string>;
  subLoads: Record<string, SubLoadState>;
  activeTextid: string | null;
  onToggle: (code: string) => void;
}

function CategoryNodeRow({
  node,
  depth,
  isOpen,
  openSubs,
  subLoads,
  activeTextid,
  onToggle,
}: CategoryNodeRowProps) {
  const empty = node.bundle_count === 0;
  const load = subLoads[node.code];
  const indent = 14 + depth * 14;
  return (
    <div>
      <div
        className={`cat-sub${empty ? " cat-empty" : ""}`}
        style={{ paddingLeft: indent }}
        onClick={() => !empty && onToggle(node.code)}
        title={node.label}
      >
        <span className="cat-caret">{empty ? "·" : isOpen ? "▾" : "▸"}</span>
        <span className="cat-zh">{node.zh}</span>
        <span className="cat-code">{node.code}</span>
        <span className="cat-count">{node.bundle_count}</span>
      </div>
      {isOpen &&
        node.subcategories.map((child) => (
          <CategoryNodeRow
            key={child.code}
            node={child}
            depth={depth + 1}
            isOpen={openSubs.has(child.code)}
            openSubs={openSubs}
            subLoads={subLoads}
            activeTextid={activeTextid}
            onToggle={onToggle}
          />
        ))}
      {isOpen && load?.status === "loading" && (
        <div className="empty">Loading…</div>
      )}
      {isOpen && load?.status === "error" && (
        <div className="empty">Failed: {load.error}</div>
      )}
      {isOpen && load?.status === "ok" && load.matches.length === 0 && (
        <div className="empty">No bundles match the filter.</div>
      )}
      {isOpen && load?.status === "ok" &&
        load.matches.map((m) => (
          <CatalogBundleRow
            key={m.textid}
            match={m}
            active={m.textid === activeTextid}
            paddingLeft={indent + 24}
          />
        ))}
    </div>
  );
}

function TimelineView({
  timeline,
  error,
  openBuckets,
  bucketLoads,
  activeTextid,
  onToggleBucket,
}: {
  timeline: TimelineResponse | null;
  error: string | null;
  openBuckets: Set<string>;
  bucketLoads: Record<string, SubLoadState>;
  activeTextid: string | null;
  onToggleBucket: (bucket: TimelineBucket) => void;
}) {
  if (error) return <div className="empty">Failed to load timeline: {error}</div>;
  if (!timeline) return <div className="empty">Loading timeline…</div>;
  if (timeline.buckets.length === 0) {
    return <div className="empty">No dated catalog entries.</div>;
  }
  return (
    <div>
      {timeline.buckets.map((bucket) => {
        const isOpen = openBuckets.has(bucket.key);
        const load = bucketLoads[bucket.key];
        return (
          <div key={bucket.key}>
            <div className="cat-sub" onClick={() => onToggleBucket(bucket)}>
              <span className="cat-caret">{isOpen ? "▾" : "▸"}</span>
              <span className="cat-zh">{bucket.label}</span>
              <span className="cat-code">
                {bucket.start}..{bucket.end}
              </span>
              <span className="cat-count">{bucket.bundle_count}</span>
            </div>
            {isOpen && (
              <CatalogMatchList
                state={load ?? { status: "idle" }}
                activeTextid={activeTextid}
                empty="No bundles in this century."
                paddingLeft={38}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function CatalogMatchList({
  state,
  activeTextid,
  empty,
  paddingLeft = 12,
}: {
  state: SubLoadState;
  activeTextid: string | null;
  empty: string;
  paddingLeft?: number;
}) {
  if (state.status === "idle" || state.status === "loading") {
    return <div className="empty">Loading…</div>;
  }
  if (state.status === "error") {
    return <div className="empty">Failed: {state.error}</div>;
  }
  if (state.matches.length === 0) {
    return <div className="empty">{empty}</div>;
  }
  return (
    <div>
      {state.matches.map((m) => (
        <CatalogBundleRow
          key={m.textid}
          match={m}
          active={m.textid === activeTextid}
          paddingLeft={paddingLeft}
        />
      ))}
    </div>
  );
}

function CatalogBundleRow({
  match,
  active,
  paddingLeft,
}: {
  match: CatalogMatch;
  active: boolean;
  paddingLeft: number;
}) {
  const lists = useWorkspace((s) => s.textLists);
  const addToList = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    if (lists.length === 0) {
      const name = window.prompt("Create list", "New list");
      if (!name) return;
      void workspace.createTextList(name).then(() => {
        const path = listPathFromName(name);
        return workspace.addTextToList(path, match.textid);
      });
      return;
    }
    const choice = window.prompt(
      `Add ${match.textid} to list`,
      lists[0]?.name ?? "",
    );
    if (!choice) return;
    const list = lists.find((item) => item.name === choice || item.path === choice);
    if (list) void workspace.addTextToList(list.path, match.textid);
  };
  return (
    <div
      className={`list-item cat-bundle${active ? " on" : ""}`}
      style={{ paddingLeft }}
      onClick={() => workspace.selectBundle(match.textid)}
      title={match.canonical_identifier ?? match.textid}
    >
      <div className="list-cjk">{(match.title ?? "").slice(0, 2) || "·"}</div>
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
          {match.title ?? match.textid}
        </div>
        <div className="list-sub">
          {match.textid}
          {match.edition_short ? ` · ${match.edition_short}` : ""}
        </div>
      </div>
      <button
        type="button"
        className="cat-add-list"
        title="Add to list"
        onClick={addToList}
      >
        +
      </button>
    </div>
  );
}
