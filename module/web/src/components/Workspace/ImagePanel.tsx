import { useEffect, useMemo, useState } from "react";
import { getJuan, getManifest } from "../../api/client";
import type { Juan, JuanMarker, Manifest } from "../../api/types";
import { resolveImage, type PageBreak } from "../../lib/imageResolver";
import { useWorkspace, workspace } from "../../state/useWorkspace";

interface Props {
  textid: string;
  seq: number;
}

interface PageEntry {
  id: string;
  offset: number;
  image?: string;
}

function pageMarkers(juan: Juan | null): JuanMarker[] {
  return ((juan?.body?.markers ?? []) as JuanMarker[]).filter(
    (m) => m.type === "page-break",
  );
}

// All page-break markers for the active edition, sorted by offset. Used for
// prev/next navigation and for resolving currentPage.markerId (which may name
// any witness at the same offset) to the witness we actually want to display.
function editionPages(
  markers: JuanMarker[],
  textid: string,
  edition: string | null,
): PageEntry[] {
  const prefix = edition ? `${textid}_${edition}_` : null;
  const entries: PageEntry[] = [];
  for (const m of markers) {
    const id = typeof m.id === "string" ? m.id : "";
    if (!id) continue;
    if (prefix && !id.startsWith(prefix)) continue;
    const offset = typeof m.offset === "number" ? m.offset : 0;
    const image = typeof m.image === "string" ? m.image : undefined;
    entries.push({ id, offset, image });
  }
  entries.sort((a, b) => a.offset - b.offset);
  return entries;
}

function scrollAnchorIntoView(pageId: string): void {
  const ec = document.querySelector<HTMLElement>(".ec");
  const el = ec?.querySelector<HTMLElement>(`.page-anchor[data-page-id="${pageId}"]`);
  if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
}

export function ImagePanel({ textid, seq }: Props) {
  const [juan, setJuan] = useState<Juan | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imgState, setImgState] = useState<"loading" | "ok" | "error">("loading");
  const currentPage = useWorkspace((s) => s.currentPage);

  useEffect(() => {
    let cancelled = false;
    setJuan(null);
    setManifest(null);
    setError(null);
    Promise.all([getJuan(textid, seq), getManifest(textid).catch(() => null)])
      .then(([j, m]) => {
        if (cancelled) return;
        setJuan(j);
        setManifest(m);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [textid, seq]);

  const edition: string | null = useMemo(() => {
    const meta = manifest?.metadata;
    if (!meta) return null;
    if (typeof meta.base_edition === "string") return meta.base_edition;
    if (meta.edition && typeof meta.edition.short === "string") return meta.edition.short;
    return null;
  }, [manifest]);

  const pages = useMemo(
    () => editionPages(pageMarkers(juan), textid, edition),
    [juan, textid, edition],
  );

  // Pick the page entry that matches the active edition for currentPage's
  // offset. currentPage.markerId may identify a different witness at the same
  // offset; the offset-based lookup keeps us aligned with the active edition.
  const activePage: PageEntry | null = useMemo(() => {
    if (!currentPage || currentPage.textid !== textid || currentPage.seq !== seq) {
      return pages[0] ?? null;
    }
    const byOffset = pages.find((p) => p.offset === currentPage.offset);
    if (byOffset) return byOffset;
    const byId = pages.find((p) => p.id === currentPage.markerId);
    return byId ?? pages[0] ?? null;
  }, [pages, currentPage, textid, seq]);

  const spec = useMemo(() => {
    if (!activePage) return { kind: "none" as const, reason: "no page-break markers" };
    return resolveImage(activePage as PageBreak, manifest, edition);
  }, [activePage, manifest, edition]);

  const url = spec.kind === "direct" ? spec.url : null;
  useEffect(() => {
    if (url) setImgState("loading");
  }, [url]);

  const idx = activePage ? pages.findIndex((p) => p.id === activePage.id) : -1;
  const canPrev = idx > 0;
  const canNext = idx >= 0 && idx < pages.length - 1;

  const goto = (target: PageEntry) => {
    workspace.setCurrentPage({
      textid,
      seq,
      markerId: target.id,
      offset: target.offset,
    });
    scrollAnchorIntoView(target.id);
  };

  const onPrev = () => {
    if (canPrev) goto(pages[idx - 1]);
  };
  const onNext = () => {
    if (canNext) goto(pages[idx + 1]);
  };

  const pageLabel = (() => {
    if (!activePage) return "—";
    const id = activePage.id;
    return id.startsWith(`${textid}_`) ? id.slice(textid.length + 1) : id;
  })();

  if (error) {
    return <div className="empty-pane">Failed to load: {error}</div>;
  }

  return (
    <div className="image-panel">
      <div className="image-panel-toolbar">
        <button
          className="ipb"
          onClick={onPrev}
          disabled={!canPrev}
          title="Previous page"
        >
          ‹
        </button>
        <span className="image-panel-label" title={activePage?.id}>
          {pageLabel}
        </span>
        <button
          className="ipb"
          onClick={onNext}
          disabled={!canNext}
          title="Next page"
        >
          ›
        </button>
        <span className="image-panel-spacer" />
        <span className="image-panel-counter">
          {idx >= 0 ? `${idx + 1} / ${pages.length}` : ""}
        </span>
      </div>
      <div className="image-panel-body">
        {spec.kind === "direct" ? (
          <>
            {imgState === "loading" && (
              <div className="image-panel-status">Loading…</div>
            )}
            {imgState === "error" && (
              <div className="image-panel-status" title={spec.url}>
                Image unavailable
              </div>
            )}
            <img
              key={spec.url}
              className="image-panel-img"
              src={spec.url}
              alt={spec.pageId}
              title={spec.url}
              style={imgState === "ok" ? undefined : { display: "none" }}
              onLoad={() => setImgState("ok")}
              onError={() => setImgState("error")}
            />
          </>
        ) : (
          <div className="empty-pane">{spec.reason}</div>
        )}
      </div>
    </div>
  );
}
