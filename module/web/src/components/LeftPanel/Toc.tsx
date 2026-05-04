import { useEffect, useState } from "react";
import { getManifest } from "../../api/client";
import type { Manifest, ManifestPart, TocEntry } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { parseMarkerId } from "../../lib/markers";

interface JuanItem {
  seq: number;
  label: string;
  marker_id?: string;
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

export function Toc() {
  const activeTextid = useWorkspace((s) => s.activeTextid);
  const activeSeq = useWorkspace((s) => s.activeSeq);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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

  if (!activeTextid) {
    return (
      <div className="empty">Select a bundle from the catalog to see its juan list.</div>
    );
  }
  if (error) return <div className="empty">Failed to load manifest: {error}</div>;
  if (!manifest) return <div className="empty">Loading…</div>;

  const items = buildItems(manifest);
  const title = manifest.metadata?.title ?? activeTextid;

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
      {items.length === 0 && <div className="empty">No juan listed in manifest.</div>}
      {items.map((it) => {
        const parsed = it.marker_id ? parseMarkerId(it.marker_id) : null;
        const sub = parsed
          ? `${parsed.edition} · ${parsed.location}`
          : `seq ${it.seq}`;
        return (
          <div
            key={it.seq}
            className={`toc-item${it.seq === activeSeq ? " on" : ""}`}
            onClick={() => workspace.openJuan(activeTextid, it.seq)}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="toc-cjk">{it.label}</div>
              <div className="toc-sub">{sub}</div>
            </div>
            <span className="toc-n">{it.seq}</span>
          </div>
        );
      })}
    </div>
  );
}
