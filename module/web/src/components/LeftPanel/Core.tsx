import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getCoreCollections,
  getCoreList,
  getCoreSuperEntry,
  getCoreWordRelationRelTypes,
} from "../../api/client";
import type {
  CoreCollectionInfo,
  CoreMatch,
  CoreSuperEntryExpansion,
  CoreSuperEntryMatch,
  CoreWordRelationRelType,
} from "../../api/types";
import { workspace } from "../../state/useWorkspace";

type ListState =
  | { status: "idle" }
  | { status: "loading" }
  | {
      status: "ok";
      matches: CoreMatch[];
      superEntries: CoreSuperEntryMatch[];
      total: number;
    }
  | { status: "error"; error: string };

type ExpandState =
  | { status: "loading" }
  | { status: "ok"; data: CoreSuperEntryExpansion }
  | { status: "error"; error: string };

export function Core() {
  const [collections, setCollections] = useState<CoreCollectionInfo[] | null>(null);
  const [collectionsError, setCollectionsError] = useState<string | null>(null);
  const [active, setActive] = useState<string>("concepts");
  const [filter, setFilter] = useState("");
  const [debouncedFilter, setDebouncedFilter] = useState("");
  const [list, setList] = useState<ListState>({ status: "idle" });
  const [expansions, setExpansions] = useState<Record<string, ExpandState>>({});
  const [relTypes, setRelTypes] = useState<CoreWordRelationRelType[] | null>(null);
  const [relType, setRelType] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    getCoreCollections()
      .then((r) => {
        if (cancelled) return;
        setCollections(r.collections);
        if (r.collections.length > 0 && !r.collections.some((c) => c.id === active)) {
          setActive(r.collections[0].id);
        }
      })
      .catch((e) => {
        if (!cancelled) setCollectionsError(String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setDebouncedFilter(filter.trim());
    }, 200);
    return () => window.clearTimeout(handle);
  }, [filter]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setList({ status: "loading" });
    setExpansions({});
    // Bibliography is shown as a hierarchical list grouped by the first
    // letter of citation_label — fetch enough to cover the whole collection.
    const limit = active === "bibliography" ? 2000 : 200;
    const relTypeParam =
      active === "word-relations" && relType ? relType : undefined;
    getCoreList(active, {
      q: debouncedFilter || undefined,
      rel_type: relTypeParam,
      limit,
    })
      .then((r) => {
        if (cancelled) return;
        setList({
          status: "ok",
          matches: r.matches ?? [],
          superEntries: r.super_entries ?? [],
          total: r.total,
        });
      })
      .catch((e) => {
        if (!cancelled) setList({ status: "error", error: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [active, debouncedFilter, relType]);

  useEffect(() => {
    if (active !== "word-relations") return;
    if (relTypes != null) return;
    let cancelled = false;
    getCoreWordRelationRelTypes()
      .then((r) => {
        if (!cancelled) setRelTypes(r.rel_types);
      })
      .catch(() => {
        if (!cancelled) setRelTypes([]);
      });
    return () => {
      cancelled = true;
    };
  }, [active, relTypes]);

  const toggleSuperEntry = useCallback((uuid: string) => {
    setExpansions((prev) => {
      if (prev[uuid]) {
        const next = { ...prev };
        delete next[uuid];
        return next;
      }
      return { ...prev, [uuid]: { status: "loading" } };
    });
    if (expansions[uuid]) return;
    getCoreSuperEntry(uuid)
      .then((data) => {
        setExpansions((prev) =>
          prev[uuid] ? { ...prev, [uuid]: { status: "ok", data } } : prev,
        );
      })
      .catch((e) => {
        setExpansions((prev) =>
          prev[uuid] ? { ...prev, [uuid]: { status: "error", error: String(e) } } : prev,
        );
      });
  }, [expansions]);

  if (collectionsError) {
    return <div className="empty">Failed to load CORE: {collectionsError}</div>;
  }
  if (!collections) {
    return <div className="empty">Loading collections…</div>;
  }
  if (collections.length === 0) {
    return <div className="empty">No CORE collections available.</div>;
  }

  const searchPlaceholder =
    active === "words" ? "Search by graph…" : "Search labels…";

  return (
    <div>
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 2,
          background: "var(--bg-pan)",
          borderBottom: "1px solid var(--bd)",
        }}
      >
        <div style={{ padding: "6px 8px" }}>
          <select
            value={active}
            onChange={(e) => {
              setActive(e.target.value);
              setFilter("");
              setRelType("");
            }}
            style={{
              width: "100%",
              padding: "4px 6px",
              fontSize: 12,
              background: "var(--bg-1)",
              color: "var(--t1)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
            }}
            aria-label="Core collection"
          >
            {collections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label} ({c.count})
              </option>
            ))}
          </select>
        </div>
        {active === "word-relations" && (
          <div style={{ padding: "0 8px 6px" }}>
            <select
              value={relType}
              onChange={(e) => setRelType(e.target.value)}
              disabled={relTypes == null}
              style={{
                width: "100%",
                padding: "4px 6px",
                fontSize: 12,
                background: "var(--bg-1)",
                color: "var(--t1)",
                border: "1px solid var(--bd)",
                borderRadius: 3,
              }}
              aria-label="Word relation category"
            >
              <option value="">All categories</option>
              {(relTypes ?? []).map((r) => (
                <option key={r.rel_type} value={r.rel_type}>
                  {r.rel_type} ({r.count})
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="cat-filter">
        <input
          type="text"
          placeholder={searchPlaceholder}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Search core records"
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
      </div>
      <CoreList
        collection={active}
        query={debouncedFilter}
        state={list}
        expansions={expansions}
        onToggleSuperEntry={toggleSuperEntry}
      />
    </div>
  );
}

function CoreList({
  collection,
  query,
  state,
  expansions,
  onToggleSuperEntry,
}: {
  collection: string;
  query: string;
  state: ListState;
  expansions: Record<string, ExpandState>;
  onToggleSuperEntry: (uuid: string) => void;
}) {
  if (state.status === "idle" || state.status === "loading") {
    return <div className="empty">Loading…</div>;
  }
  if (state.status === "error") {
    return <div className="empty">Failed: {state.error}</div>;
  }
  if (collection === "words") {
    if (state.superEntries.length === 0) {
      return <div className="empty">No super-entries match.</div>;
    }
    return (
      <div>
        {state.superEntries.map((se) => (
          <SuperEntryRow
            key={se.super_entry_uuid}
            entry={se}
            expansion={expansions[se.super_entry_uuid]}
            onToggle={onToggleSuperEntry}
          />
        ))}
      </div>
    );
  }
  if (state.matches.length === 0) {
    return <div className="empty">No records match.</div>;
  }
  if (collection === "bibliography" && !query) {
    return <BibliographyList matches={state.matches} />;
  }
  return (
    <div>
      {state.matches.map((m) => (
        <CoreRow key={m.uuid} match={m} collection={collection} />
      ))}
    </div>
  );
}

function bibliographyLetter(label: string): string {
  const trimmed = label.trim();
  if (!trimmed) return "#";
  const ch = trimmed[0].toUpperCase();
  return /[A-Z]/.test(ch) ? ch : "#";
}

function BibliographyList({ matches }: { matches: CoreMatch[] }) {
  const buckets = useMemo(() => {
    const m = new Map<string, CoreMatch[]>();
    for (const item of matches) {
      const key = bibliographyLetter(item.display_label);
      const bucket = m.get(key);
      if (bucket) bucket.push(item);
      else m.set(key, [item]);
    }
    return Array.from(m.entries()).sort(([a], [b]) => {
      if (a === "#") return 1;
      if (b === "#") return -1;
      return a.localeCompare(b);
    });
  }, [matches]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <div>
      {buckets.map(([letter, items]) => {
        const isOpen = !!open[letter];
        return (
          <div key={letter}>
            <div
              className="cat-sub"
              onClick={() => setOpen((p) => ({ ...p, [letter]: !p[letter] }))}
              title={letter}
            >
              <span className="cat-caret">{isOpen ? "▾" : "▸"}</span>
              <span className="cat-zh">{letter}</span>
              <span className="cat-count">{items.length}</span>
            </div>
            {isOpen &&
              items.map((m) => (
                <div
                  key={m.uuid}
                  className="list-item"
                  style={{ paddingLeft: 38 }}
                  onClick={() => workspace.openCoreRecord("bibliography", m.uuid)}
                  title={m.display_label}
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
                      {m.display_label}
                    </div>
                    {m.alt_labels.length > 0 && (
                      <div className="list-sub">{m.alt_labels.join(" · ")}</div>
                    )}
                  </div>
                </div>
              ))}
          </div>
        );
      })}
    </div>
  );
}

function CoreRow({ match, collection }: { match: CoreMatch; collection: string }) {
  return (
    <div
      className="list-item"
      style={{ paddingLeft: 14 }}
      onClick={() => workspace.openCoreRecord(collection, match.uuid)}
      title={match.display_label}
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
          {match.display_label}
        </div>
        {match.alt_labels.length > 0 && (
          <div className="list-sub">{match.alt_labels.join(" · ")}</div>
        )}
      </div>
    </div>
  );
}

function SuperEntryRow({
  entry,
  expansion,
  onToggle,
}: {
  entry: CoreSuperEntryMatch;
  expansion: ExpandState | undefined;
  onToggle: (uuid: string) => void;
}) {
  const open = expansion != null;
  return (
    <div>
      <div
        className="cat-sub"
        onClick={() => onToggle(entry.super_entry_uuid)}
        title={entry.orth}
      >
        <span className="cat-caret">{open ? "▾" : "▸"}</span>
        <span className="cat-zh">{entry.orth}</span>
        <span className="cat-count">{entry.word_count}</span>
      </div>
      {open && expansion?.status === "loading" && (
        <div className="empty">Loading…</div>
      )}
      {open && expansion?.status === "error" && (
        <div className="empty">Failed: {expansion.error}</div>
      )}
      {open && expansion?.status === "ok" && (
        expansion.data.words.length === 0 ? (
          <div className="empty">(no words)</div>
        ) : (
          expansion.data.words.map((w) => (
            <div
              key={w.uuid}
              className="list-item"
              style={{ paddingLeft: 38 }}
              onClick={() => workspace.openCoreRecord("words", w.uuid)}
              title={w.display_label ?? w.uuid}
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
                  {w.display_label ?? `${entry.orth} : ${w.concept ?? "?"}`}
                </div>
                {w.n && <div className="list-sub">n={w.n}</div>}
              </div>
            </div>
          ))
        )
      )}
    </div>
  );
}
