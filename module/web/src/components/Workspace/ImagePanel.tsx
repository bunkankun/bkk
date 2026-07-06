import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent,
  type WheelEvent,
} from "react";
import { getJuan, getManifest } from "../../api/client";
import type { Juan, JuanMarker, Manifest } from "../../api/types";
import { resolveImage, type PageBreak } from "../../lib/imageResolver";
import { parseMarkerId } from "../../lib/markers";
import { useWorkspace, workspace } from "../../state/useWorkspace";

const BUCKETS = ["front", "body", "back"] as const;
type BucketName = (typeof BUCKETS)[number];
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.25;

interface Props {
  textid: string;
  seq: number;
  paneId?: string;
  tabId?: string;
}

interface PageEntry {
  bucket: BucketName;
  edition: string | null;
  id: string;
  offset: number;
  image?: string;
}

interface ImageEdition {
  short: string;
  label: string;
  count: number;
}

function pageMarkers(juan: Juan | null): { bucket: BucketName; marker: JuanMarker }[] {
  if (!juan) return [];
  return BUCKETS.flatMap((bucket) =>
    ((juan[bucket]?.markers ?? []) as JuanMarker[])
      .filter((m) => m.type === "page-break")
      .map((marker) => ({ bucket, marker })),
  );
}

function imageBaseFor(manifest: Manifest | null, edition: string | null): string | undefined {
  if (!manifest || !edition) return undefined;
  const bases = manifest.metadata?.image_base_urls;
  return bases?.[edition] ?? bases?.[edition.toLowerCase()] ?? bases?.[edition.toUpperCase()];
}

function editionLabel(manifest: Manifest | null, short: string): string {
  const top = manifest?.editions?.find((ed) => ed.short === short)?.label;
  if (top) return top;
  const metadataEditions = manifest?.metadata?.editions;
  if (Array.isArray(metadataEditions)) {
    const entry = metadataEditions.find(
      (ed) => ed && typeof ed === "object" && "short" in ed && ed.short === short,
    );
    if (
      entry &&
      typeof entry === "object" &&
      "label" in entry &&
      typeof entry.label === "string"
    ) {
      return entry.label;
    }
  }
  return short;
}

// All page-break markers for the active edition, sorted by offset. Used for
// prev/next navigation and for resolving currentPage.markerId (which may name
// any witness at the same offset) to the witness we actually want to display.
function pageEntries(markers: { bucket: BucketName; marker: JuanMarker }[]): PageEntry[] {
  const entries: PageEntry[] = [];
  for (const { bucket, marker: m } of markers) {
    const id = typeof m.id === "string" ? m.id : "";
    if (!id) continue;
    const edition = parseMarkerId(id)?.edition ?? null;
    const offset = typeof m.offset === "number" ? m.offset : 0;
    const image = typeof m.image === "string" ? m.image : undefined;
    entries.push({ bucket, edition, id, offset, image });
  }
  entries.sort((a, b) => {
    const bucketDelta = BUCKETS.indexOf(a.bucket) - BUCKETS.indexOf(b.bucket);
    return bucketDelta || a.offset - b.offset;
  });
  return entries;
}

function imageEditions(entries: PageEntry[], manifest: Manifest | null): ImageEdition[] {
  const counts = new Map<string, number>();
  for (const page of entries) {
    if (!page.edition || !page.image || !imageBaseFor(manifest, page.edition)) continue;
    counts.set(page.edition, (counts.get(page.edition) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([short, count]) => ({ short, label: editionLabel(manifest, short), count }))
    .sort((a, b) => a.short.localeCompare(b.short));
}

function scrollAnchorIntoView(pageId: string, paneId?: string, tabId?: string): void {
  const roots = Array.from(document.querySelectorAll<HTMLElement>(".ec"));
  const ec =
    roots.find(
      (el) =>
        (paneId == null || el.dataset.paneId === paneId) &&
        (tabId == null || el.dataset.tabId === tabId),
    ) ?? roots[0] ?? null;
  const el = ec?.querySelector<HTMLElement>(`.page-anchor[data-page-id="${pageId}"]`);
  if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
}

function clampZoom(value: number): number {
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Number(value.toFixed(2))));
}

export function ImagePanel({ textid, seq, paneId, tabId }: Props) {
  const [juan, setJuan] = useState<Juan | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imgState, setImgState] = useState<"loading" | "ok" | "error">("loading");
  const [selectedEdition, setSelectedEdition] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const panRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    scrollLeft: number;
    scrollTop: number;
  } | null>(null);
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

  useEffect(() => {
    setSelectedEdition(null);
  }, [textid]);

  const allPages = useMemo(() => pageEntries(pageMarkers(juan)), [juan]);
  const editionsWithImages = useMemo(
    () => imageEditions(allPages, manifest),
    [allPages, manifest],
  );

  const defaultEdition: string | null = useMemo(() => {
    const markerEdition =
      currentPage && currentPage.textid === textid && currentPage.seq === seq
        ? parseMarkerId(currentPage.markerId)?.edition ?? null
        : null;
    if (markerEdition && imageBaseFor(manifest, markerEdition)) return markerEdition;
    if (editionsWithImages[0]) return editionsWithImages[0].short;
    const meta = manifest?.metadata;
    if (!meta) return null;
    if (typeof meta.base_edition === "string") return meta.base_edition;
    if (meta.edition && typeof meta.edition.short === "string") return meta.edition.short;
    return null;
  }, [currentPage, editionsWithImages, manifest, textid, seq]);

  const edition = selectedEdition ?? defaultEdition;

  const pages = useMemo(
    () => allPages.filter((page) => page.edition === edition),
    [allPages, edition],
  );

  // Prefer the exact page-break id, because KRP-shaped ids carry the edition
  // that chooses the image base URL. Offset lookup is only a fallback for
  // older/currentPage states that did not preserve marker identity.
  const activePage: PageEntry | null = useMemo(() => {
    if (!currentPage || currentPage.textid !== textid || currentPage.seq !== seq) {
      return pages[0] ?? null;
    }
    const byId = pages.find((p) => p.id === currentPage.markerId);
    if (byId) return byId;
    const byOffset = pages.find(
      (p) => p.bucket === currentPage.bucket && p.offset === currentPage.offset,
    );
    if (byOffset) return byOffset;
    return pages[0] ?? null;
  }, [pages, currentPage, textid, seq]);

  const spec = useMemo(() => {
    if (!activePage) return { kind: "none" as const, reason: "no page-break markers" };
    return resolveImage(activePage as PageBreak, manifest, edition, textid);
  }, [activePage, manifest, edition, textid]);

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
      bucket: target.bucket,
      markerId: target.id,
      offset: target.offset,
    });
    scrollAnchorIntoView(target.id, paneId, tabId);
  };

  const onPrev = () => {
    if (canPrev) goto(pages[idx - 1]);
  };
  const onNext = () => {
    if (canNext) goto(pages[idx + 1]);
  };

  const onEditionChange = (next: string) => {
    setSelectedEdition(next || null);
  };

  const zoomOut = useCallback(() => {
    setZoom((z) => clampZoom(z - ZOOM_STEP));
  }, []);

  const zoomIn = useCallback(() => {
    setZoom((z) => clampZoom(z + ZOOM_STEP));
  }, []);

  const resetZoom = useCallback(() => {
    setZoom(1);
  }, []);

  const onImageWheel = useCallback((ev: WheelEvent<HTMLDivElement>) => {
    if (!ev.ctrlKey && !ev.metaKey) return;
    ev.preventDefault();
    setZoom((z) => clampZoom(z + (ev.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)));
  }, []);

  const startPan = useCallback((ev: PointerEvent<HTMLDivElement>) => {
    if (ev.button !== 0 || zoom === 1) return;
    ev.preventDefault();
    const el = ev.currentTarget;
    panRef.current = {
      pointerId: ev.pointerId,
      startX: ev.clientX,
      startY: ev.clientY,
      scrollLeft: el.scrollLeft,
      scrollTop: el.scrollTop,
    };
    el.setPointerCapture(ev.pointerId);
    setIsPanning(true);
  }, [zoom]);

  const movePan = useCallback((ev: PointerEvent<HTMLDivElement>) => {
    const pan = panRef.current;
    if (!pan) return;
    ev.preventDefault();
    const el = ev.currentTarget;
    el.scrollLeft = pan.scrollLeft - (ev.clientX - pan.startX);
    el.scrollTop = pan.scrollTop - (ev.clientY - pan.startY);
  }, []);

  const stopPan = useCallback((ev: PointerEvent<HTMLDivElement>) => {
    const pan = panRef.current;
    if (!pan) return;
    try {
      ev.currentTarget.releasePointerCapture(pan.pointerId);
    } catch {
      /* pointer capture may already be gone */
    }
    panRef.current = null;
    setIsPanning(false);
  }, []);

  const pageLabel = (() => {
    if (!activePage) return "—";
    const id = activePage.id;
    return id.startsWith(`${textid}_`) ? id.slice(textid.length + 1) : id;
  })();
  const zoomLabel = `${Math.round(zoom * 100)}%`;
  const imageStyle =
    zoom === 1
      ? imgState === "ok"
        ? undefined
        : { display: "none" }
      : {
          display: imgState === "ok" ? "block" : "none",
          width: `${zoom * 100}%`,
          maxWidth: "none",
          maxHeight: "none",
        };

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
        {editionsWithImages.length > 0 ? (
          <select
            className="image-panel-edition"
            value={edition ?? ""}
            onChange={(e) => onEditionChange(e.target.value)}
            title="Image edition"
          >
            {editionsWithImages.map((ed) => (
              <option key={ed.short} value={ed.short}>
                {ed.short}{ed.label !== ed.short ? ` · ${ed.label}` : ""}
              </option>
            ))}
          </select>
        ) : null}
        <button
          className="ipb"
          onClick={onNext}
          disabled={!canNext}
          title="Next page"
        >
          ›
        </button>
        <span className="image-panel-spacer" />
        <button
          className="ipb"
          onClick={zoomOut}
          disabled={zoom <= ZOOM_MIN}
          title="Zoom out"
        >
          −
        </button>
        <button className="ipb zoom-reset" onClick={resetZoom} title="Fit image">
          {zoomLabel}
        </button>
        <button
          className="ipb"
          onClick={zoomIn}
          disabled={zoom >= ZOOM_MAX}
          title="Zoom in"
        >
          +
        </button>
        <span className="image-panel-counter">
          {idx >= 0 ? `${idx + 1} / ${pages.length}` : ""}
        </span>
      </div>
      <div
        className={`image-panel-body${zoom === 1 ? "" : " zoomed"}${
          isPanning ? " panning" : ""
        }`}
        onWheel={onImageWheel}
        onPointerDown={startPan}
        onPointerMove={movePan}
        onPointerUp={stopPan}
        onPointerCancel={stopPan}
      >
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
              draggable={false}
              style={imageStyle}
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
