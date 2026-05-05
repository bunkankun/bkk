import { useEffect, useMemo, useState } from "react";
import { getCatalog } from "../../api/client";
import type { CatalogMatch, CatalogResponse } from "../../api/types";
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

export function Catalog() {
  const [data, setData] = useState<CatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const activeTextid = useWorkspace((s) => s.activeTextid);

  useEffect(() => {
    let cancelled = false;
    getCatalog({ limit: 50 })
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = useMemo(() => {
    if (!data) return [];
    const needle = filter.trim();
    return needle ? data.matches.filter((m) => matchesFilter(m, needle)) : data.matches;
  }, [data, filter]);

  if (error) return <div className="empty">Failed to load catalog: {error}</div>;
  if (!data) return <div className="empty">Loading catalog…</div>;

  return (
    <div>
      <div className="cat-filter">
        <input
          type="text"
          placeholder="Filter catalog…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter catalog"
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
      {data.matches.length === 0 ? (
        <div className="empty">No bundles match these filters.</div>
      ) : visible.length === 0 ? (
        <div className="empty">No bundles match “{filter}”.</div>
      ) : (
        visible.map((m) => (
          <div
            key={m.textid}
            className={`list-item${m.textid === activeTextid ? " on" : ""}`}
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
        ))
      )}
    </div>
  );
}
