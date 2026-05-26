import { useCallback, useEffect, useMemo, useState } from "react";
import { getCatalog, getCategories } from "../../api/client";
import type {
  CatalogMatch,
  CategoriesResponse,
  CategoryNode,
} from "../../api/types";
import { workspace, useWorkspace } from "../../state/useWorkspace";

function matchesFilter(m: CatalogMatch, needle: string): boolean {
  if (!needle) return true;
  const haystacks: string[] = [m.textid];
  if (m.title) haystacks.push(m.title);
  if (m.canonical_identifier) haystacks.push(m.canonical_identifier);
  if (m.edition_short) haystacks.push(m.edition_short);
  const meta = m.metadata as {
    alt_titles?: unknown;
    authors?: unknown;
    identifiers?: Record<string, unknown>;
  };
  const altTitles = Array.isArray(meta.alt_titles) ? meta.alt_titles : [];
  for (const t of altTitles) if (typeof t === "string") haystacks.push(t);
  const authors = Array.isArray(meta.authors) ? meta.authors : [];
  for (const a of authors) {
    if (a && typeof a === "object" && "name" in a) {
      const name = (a as { name?: unknown }).name;
      if (typeof name === "string") haystacks.push(name);
    }
  }
  if (meta.identifiers && typeof meta.identifiers === "object") {
    for (const v of Object.values(meta.identifiers)) {
      if (typeof v === "string") haystacks.push(v);
      else if (Array.isArray(v))
        for (const x of v) if (typeof x === "string") haystacks.push(x);
    }
  }
  const n = needle.toLowerCase();
  return haystacks.some((h) => h.toLowerCase().includes(n));
}

type SubLoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; matches: CatalogMatch[] }
  | { status: "error"; error: string };

export function Catalog() {
  const [cats, setCats] = useState<CategoriesResponse | null>(null);
  const [catsError, setCatsError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [openTops, setOpenTops] = useState<Set<string>>(() => new Set());
  const [openSubs, setOpenSubs] = useState<Set<string>>(() => new Set());
  const [subLoads, setSubLoads] = useState<Record<string, SubLoadState>>({});
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

  const filterNeedle = filter.trim();
  const filteredSubLoads = useMemo(() => {
    if (!filterNeedle) return subLoads;
    const out: Record<string, SubLoadState> = {};
    for (const [code, s] of Object.entries(subLoads)) {
      if (s.status !== "ok") {
        out[code] = s;
        continue;
      }
      out[code] = {
        status: "ok",
        matches: s.matches.filter((m) => matchesFilter(m, filterNeedle)),
      };
    }
    return out;
  }, [subLoads, filterNeedle]);

  if (catsError) return <div className="empty">Failed to load categories: {catsError}</div>;
  if (!cats) return <div className="empty">Loading categories…</div>;

  return (
    <div>
      <div className="cat-filter">
        <input
          type="text"
          placeholder="Filter expanded bundles…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter loaded bundles"
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
      {cats.categories.map((top) => (
        <CategoryRow
          key={top.code}
          node={top}
          depth={0}
          isOpen={openTops.has(top.code)}
          openSubs={openSubs}
          subLoads={filteredSubLoads}
          activeTextid={activeTextid}
          onToggleTop={toggleTop}
          onToggleSub={toggleSub}
        />
      ))}
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
          <div
            key={m.textid}
            className={`list-item cat-bundle${m.textid === activeTextid ? " on" : ""}`}
            style={{ paddingLeft: indent + 24 }}
            onClick={() => workspace.selectBundle(m.textid)}
            title={m.canonical_identifier ?? m.textid}
          >
            <div className="list-cjk">{(m.title ?? "").slice(0, 2) || "·"}</div>
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
                {m.title ?? m.textid}
              </div>
              <div className="list-sub">
                {m.textid}
                {m.edition_short ? ` · ${m.edition_short}` : ""}
              </div>
            </div>
          </div>
        ))}
    </div>
  );
}
