import { useEffect, useMemo, useRef, useState } from "react";
import {
  getBundleTranslations,
  getTranslationAlignment,
} from "../../api/client";
import type {
  TranslationAlignedRow,
  TranslationAlignmentResponse,
} from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";

interface Props {
  paneId: string;
  tabId: string;
  textid: string;
  seq: number;
  translationId: string | null;
}

function rowKey(row: TranslationAlignedRow): string {
  return `${row.source_marker_id}:${row.source_offset}:${row.corresp}`;
}

export function TranslationSidecar({ paneId, tabId, textid, seq, translationId }: Props) {
  const [alignment, setAlignment] = useState<TranslationAlignmentResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noTranslationsAvailable, setNoTranslationsAvailable] = useState(false);
  const selectedSegment = useWorkspace((s) => s.selectedSegment);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rowRefs = useRef(new Map<string, HTMLElement>());
  const syncingFromTextRef = useRef(false);
  const lastTextSyncKeyRef = useRef<string>("");
  const lastTranslationSyncKeyRef = useRef<string>("");
  const [syncedCorresp, setSyncedCorresp] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setAlignment(null);
    setError(null);
    if (!translationId) return () => { cancelled = true; };
    getTranslationAlignment(textid, seq, translationId)
      .then((res) => {
        if (cancelled) return;
        setAlignment(res);
        const first = res.rows[0];
        if (first) {
          workspace.setCurrentPage({
            textid,
            seq,
            bucket: "body",
            markerId: first.source_marker_id,
            offset: first.source_offset,
          });
        }
      })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [textid, seq, translationId]);

  useEffect(() => {
    if (translationId != null) {
      setNoTranslationsAvailable(false);
      return;
    }
    let cancelled = false;
    getBundleTranslations(textid)
      .then((res) => {
        if (!cancelled) setNoTranslationsAvailable(res.translations.length === 0);
      })
      .catch(() => { /* keep generic select-translation empty state */ });
    return () => { cancelled = true; };
  }, [textid, translationId]);

  useEffect(() => {
    if (!selectedSegment || !alignment) return;
    if (selectedSegment.textid !== textid || selectedSegment.seq !== seq) return;
    const row = alignment.rows.find((r) => r.corresp === selectedSegment.corresp);
    if (!row) return;
    rowRefs.current.get(rowKey(row))?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selectedSegment, alignment, textid, seq]);

  // Track the topmost visible aligned row and report it as currentPage, so
  // image navigation and mode switches stay aligned with the source text.
  useEffect(() => {
    const root = containerRef.current;
    if (!root || alignment == null) return;
    const visible = new Map<Element, { id: string; offset: number }>();
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const el = e.target as HTMLElement;
          if (e.isIntersecting) {
            const id = el.dataset.pageId;
            const off = Number(el.dataset.pageOffset);
            if (!id || Number.isNaN(off)) continue;
            visible.set(el, { id, offset: off });
          } else {
            visible.delete(el);
          }
        }
        let best: { id: string; offset: number } | null = null;
        for (const v of visible.values()) {
          if (best == null || v.offset < best.offset) best = v;
        }
        if (best == null) return;
        workspace.setCurrentPage({
          textid,
          seq,
          bucket: "body",
          markerId: best.id,
          offset: best.offset,
        });
      },
      { root, rootMargin: "0px 0px -85% 0px" },
    );
    const rows = root.querySelectorAll<HTMLElement>("[data-page-id]");
    rows.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [alignment, textid, seq]);

  const firstTranslatedIdx = useMemo(
    () => alignment?.rows.findIndex((r) => r.translation_text && !r.continued) ?? -1,
    [alignment],
  );

  useEffect(() => {
    if (!alignment) return;
    const rowForOffset = (offset: number): TranslationAlignedRow | null => {
      let best: TranslationAlignedRow | null = null;
      for (const row of alignment.rows) {
        if (offset >= row.source_offset && offset < row.source_end) return row;
        if (row.source_offset <= offset) best = row;
        if (row.source_offset > offset) break;
      }
      return best ?? alignment.rows[0] ?? null;
    };
    const onSourceScroll = (event: Event) => {
      const detail = (event as CustomEvent<{
        paneId?: string;
        tabId?: string;
        textid?: string;
        seq?: number;
        bucket?: string;
        offset?: number;
      }>).detail;
      if (
        detail?.paneId !== paneId ||
        detail.tabId !== tabId ||
        detail.textid !== textid ||
        detail.seq !== seq ||
        detail.bucket !== "body" ||
        typeof detail.offset !== "number"
      ) {
        return;
      }
      const row = rowForOffset(detail.offset);
      if (!row) return;
      const key = rowKey(row);
      if (key === lastTextSyncKeyRef.current) return;
      lastTextSyncKeyRef.current = key;
      setSyncedCorresp(row.corresp);
      const el = rowRefs.current.get(key);
      if (!el || !containerRef.current) return;
      const rootRect = containerRef.current.getBoundingClientRect();
      const rowRect = el.getBoundingClientRect();
      if (rowRect.top >= rootRect.top && rowRect.bottom <= rootRect.bottom) return;
      syncingFromTextRef.current = true;
      el.scrollIntoView({ block: "nearest" });
      window.setTimeout(() => {
        syncingFromTextRef.current = false;
      }, 250);
    };
    window.addEventListener("bkk:source-scroll-sync", onSourceScroll);
    return () => window.removeEventListener("bkk:source-scroll-sync", onSourceScroll);
  }, [alignment, paneId, tabId, textid, seq]);

  useEffect(() => {
    const root = containerRef.current;
    if (!root || !alignment) return;
    let raf = 0;
    const syncSource = () => {
      raf = 0;
      if (syncingFromTextRef.current) return;
      const rootRect = root.getBoundingClientRect();
      const anchorY = rootRect.top + Math.min(80, Math.max(16, rootRect.height * 0.12));
      let best: TranslationAlignedRow | null = null;
      let bestDistance = Infinity;
      for (const row of alignment.rows) {
        const el = rowRefs.current.get(rowKey(row));
        if (!el) continue;
        const rect = el.getBoundingClientRect();
        if (rect.bottom < rootRect.top || rect.top > rootRect.bottom) continue;
        const distance =
          rect.top <= anchorY && rect.bottom >= anchorY
            ? 0
            : Math.min(Math.abs(rect.top - anchorY), Math.abs(rect.bottom - anchorY));
        if (distance < bestDistance) {
          best = row;
          bestDistance = distance;
        }
      }
      if (!best) return;
      const key = rowKey(best);
      if (key === lastTranslationSyncKeyRef.current) return;
      lastTranslationSyncKeyRef.current = key;
      setSyncedCorresp(best.corresp);
      workspace.highlightTextLocation({
        textid,
        seq,
        bucket: "body",
        offset: best.source_offset,
        length: Math.max(1, best.source_end - best.source_offset),
        markerId: best.source_marker_id,
        flash: false,
      });
    };
    const schedule = () => {
      if (raf) return;
      raf = window.requestAnimationFrame(syncSource);
    };
    root.addEventListener("scroll", schedule, { passive: true });
    schedule();
    return () => {
      root.removeEventListener("scroll", schedule);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [alignment, textid, seq]);

  if (!translationId) {
    return (
      <div className="translation-sidecar">
        <div className="sidecar-title">Translation</div>
        <div className="empty-pane">
          {noTranslationsAvailable
            ? "No translations available."
            : "Select a translation from Translations."}
        </div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="translation-sidecar">
        <div className="sidecar-title">Translation</div>
        <div className="empty-pane">Failed to load translation: {error}</div>
      </div>
    );
  }
  if (!alignment) {
    return (
      <div className="translation-sidecar">
        <div className="sidecar-title">Translation</div>
        <div className="empty-pane">Loading translation…</div>
      </div>
    );
  }
  if (alignment.status === "no_alignment_markers") {
    return (
      <div className="translation-sidecar">
        <div className="sidecar-title">Translation</div>
        <div className="empty-pane">No source segment markers are available for translation alignment.</div>
      </div>
    );
  }

  const title = alignment.translation?.title ?? alignment.translation?.id ?? translationId;
  const lang = alignment.translation?.language;
  const translators = (alignment.translation?.responsibility ?? [])
    .map((r) => r.name)
    .filter((n): n is string => typeof n === "string" && n.length > 0)
    .join(", ");
  const date = alignment.translation?.date;

  return (
    <div className="translation-sidecar">
      <div className="sidecar-title">
        <div className="sidecar-title-main">{title}</div>
        <div className="sidecar-title-meta">
          {lang ? `${lang}` : ""}
          {translators ? ` · ${translators}` : ""}
          {date ? ` · ${date.slice(0, 4)}` : ""}
        </div>
        {firstTranslatedIdx >= 0 && (
          <button
            className="juan-nav-btn sidecar-jump"
            onClick={() => {
              containerRef.current
                ?.querySelector<HTMLElement>("[data-first-translated]")
                ?.scrollIntoView({ block: "start", behavior: "smooth" });
            }}
          >
            ↓ first translation
          </button>
        )}
      </div>
      <div className="translation-sidecar-list" ref={containerRef}>
        {alignment.rows.map((row, rowIdx) => {
          const isActive =
            selectedSegment?.textid === textid &&
            selectedSegment.seq === seq &&
            selectedSegment.corresp === row.corresp;
          const isSynced = syncedCorresp === row.corresp;
          const key = rowKey(row);
          return (
            <button
              key={key}
              ref={(el) => {
                if (el) rowRefs.current.set(key, el);
                else rowRefs.current.delete(key);
              }}
              type="button"
              className={`translation-sidecar-row${row.continued ? " continued" : ""}${isActive ? " active" : ""}${isSynced ? " synced" : ""}`}
              data-page-id={row.source_marker_id}
              data-page-offset={row.source_offset}
              {...(rowIdx === firstTranslatedIdx ? { "data-first-translated": "1" } : {})}
              onClick={() => {
                workspace.setSelectedSegment({
                  textid,
                  seq,
                  corresp: row.corresp,
                  sourceText: row.source_text,
                });
                workspace.highlightTextLocation({
                  textid,
                  seq,
                  bucket: "body",
                  offset: row.source_offset,
                  length: Math.max(1, row.source_end - row.source_offset),
                  markerId: row.source_marker_id,
                });
              }}
            >
              <span className="trans-ref">
                {row.corresp}
                {row.resp ? <span className="trans-resp"> · {row.resp}</span> : null}
              </span>
              <span className="translation-sidecar-text">
                {row.translation_text ? (
                  row.translation_text.split("\n").map((line, i) => (
                    <span key={i} className="translation-sidecar-line">{line}</span>
                  ))
                ) : (
                  <span className="trans-missing">
                    {row.continued ? "continued" : "untranslated"}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
