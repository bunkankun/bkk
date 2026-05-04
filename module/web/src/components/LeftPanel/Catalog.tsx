import { useEffect, useState } from "react";
import { getCatalog } from "../../api/client";
import type { CatalogResponse } from "../../api/types";
import { workspace, useWorkspace } from "../../state/useWorkspace";

export function Catalog() {
  const [data, setData] = useState<CatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
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

  if (error) return <div className="empty">Failed to load catalog: {error}</div>;
  if (!data) return <div className="empty">Loading catalog…</div>;
  if (data.matches.length === 0)
    return <div className="empty">No bundles match these filters.</div>;

  return (
    <div>
      {data.matches.map((m) => (
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
      ))}
    </div>
  );
}
