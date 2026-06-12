import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  getAnnotations,
  getBundleTranslations,
  getTranslationAlignment,
} from "../../api/client";
import type {
  Annotation,
  TranslationAlignedRow,
  TranslationAlignmentResponse,
} from "../../api/types";
import { krRefToChar } from "../../lib/pua";
import { hasKrpLocation } from "../../lib/markers";
import { isResizing, useWorkspace, workspace } from "../../state/useWorkspace";
import { annTooltip, buildAnnotationIndex, type AnnotationIndex } from "./AnnotationLayer";

const PUNCT_RE = /[\u3000-\u303F\uFF00-\uFFEF：「」『』，。、！？；…—\s\u00B7]/;
const CJK_RE = /[\u3400-\u9FFF\uF900-\uFAFF]/;
const KR_REF_START_RE = /^&KR(\d+);/;

function isPua(cp: number): boolean {
  return (
    (cp >= 0xe000 && cp <= 0xf8ff) ||
    (cp >= 0xf0000 && cp <= 0xffffd) ||
    (cp >= 0x100000 && cp <= 0x10fffd)
  );
}

function isCjk(ch: string): boolean {
  return CJK_RE.test(ch);
}

interface DecodedChar {
  ch: string;
  srcOffset: number;
  srcEndOffset: number;
}

function decodeKrRefs(text: string): DecodedChar[] {
  const rawChars = [...text];
  const out: DecodedChar[] = [];
  let i = 0;
  while (i < rawChars.length) {
    const rest = rawChars.slice(i, i + 16).join("");
    const match = rest.match(KR_REF_START_RE);
    if (match) {
      out.push({ ch: krRefToChar(match[1]), srcOffset: i, srcEndOffset: i + [...match[0]].length });
      i += [...match[0]].length;
      continue;
    }
    out.push({ ch: rawChars[i], srcOffset: i, srcEndOffset: i + 1 });
    i++;
  }
  return out;
}

interface SourceTextProps {
  row: TranslationAlignedRow;
  paneId: string;
  tabId: string;
  annIndex: AnnotationIndex;
  flashOffsets: { start: number; end: number } | null;
  onAnnClick: (
    offset: number,
    endOffset: number,
    ch: string,
    row: TranslationAlignedRow,
    anns: Annotation[],
  ) => void;
}

function SourceText({
  row,
  paneId,
  tabId,
  annIndex,
  flashOffsets,
  onAnnClick,
}: SourceTextProps) {
  const chars = decodeKrRefs(row.source_text);
  return (
    <>
      {chars.map((c, i) => {
        const absOffset = row.source_offset + c.srcOffset;
        const absEndOffset = row.source_offset + c.srcEndOffset;
        if (PUNCT_RE.test(c.ch)) {
          return <span key={i} className="pu">{c.ch}</span>;
        }
        const anns = annIndex.byOffset.get(absOffset);
        const has = anns && anns.length > 0;
        const flashing =
          flashOffsets != null &&
          absOffset >= flashOffsets.start &&
          absOffset < flashOffsets.end;
        const cls = `${has ? "ch has-ann" : "ch"}${flashing ? " kwic-flash" : ""}`;
        const title = has ? anns!.map(annTooltip).join(" / ") : undefined;
        return (
          <span
            key={i}
            className={cls}
            data-offset={absOffset}
            data-end-offset={absEndOffset}
            data-bucket="body"
            data-anchor-id={row.source_marker_id}
            data-anchor-row-offset={row.source_offset}
            title={title}
            onMouseEnter={() => workspace.setHover(paneId, tabId, c.ch)}
            onClick={(ev) => {
              if (!has) return;
              const sel = window.getSelection();
              if (sel && !sel.isCollapsed) return;
              onAnnClick(absOffset, absEndOffset, c.ch, row, anns!);
              ev.stopPropagation();
            }}
          >
            {c.ch}
          </span>
        );
      })}
    </>
  );
}

interface Props {
  paneId: string;
  tabId: string;
  textid: string;
  seq: number;
  translationId: string | null;
}

export function TranslationViewer({ paneId, tabId, textid, seq, translationId }: Props) {
  const [alignment, setAlignment] = useState<TranslationAlignmentResponse | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const selectedSegment = useWorkspace((s) => s.selectedSegment);
  const pending = useWorkspace((s) => s.pendingHighlight);
  const [flashOffsets, setFlashOffsets] = useState<{ start: number; end: number } | null>(
    null,
  );
  const lastFlashedRef = useRef<typeof pending>(null);

  useEffect(() => {
    let cancelled = false;
    setAlignment(null);
    setError(null);
    if (!translationId) return () => { cancelled = true; };
    getTranslationAlignment(textid, seq, translationId)
      .then((res) => { if (!cancelled) setAlignment(res); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [textid, seq, translationId]);

  // When opened in Trans mode but nothing is selected, check whether this
  // text has any translations at all. If none, drop back to Read mode so
  // the user sees the source text instead of an empty pane.
  const [noTranslationsAvailable, setNoTranslationsAvailable] = useState(false);
  useEffect(() => {
    if (translationId != null) {
      setNoTranslationsAvailable(false);
      return;
    }
    let cancelled = false;
    getBundleTranslations(textid)
      .then((res) => {
        if (cancelled) return;
        if (res.translations.length === 0) {
          setNoTranslationsAvailable(true);
          workspace.setReadMode("read");
        }
      })
      .catch(() => { /* keep current empty-pane message */ });
    return () => { cancelled = true; };
  }, [textid, translationId]);

  useEffect(() => {
    let cancelled = false;
    setAnnotations([]);
    getAnnotations(textid, seq)
      .then((a) => { if (!cancelled) setAnnotations(a); })
      .catch(() => { if (!cancelled) setAnnotations([]); });
    return () => { cancelled = true; };
  }, [textid, seq]);

  const annIndex = useMemo(() => buildAnnotationIndex(annotations), [annotations]);

  useEffect(() => {
    if (!selectedSegment || !alignment) return;
    if (selectedSegment.textid !== textid || selectedSegment.seq !== seq) return;
    const el = containerRef.current?.querySelector<HTMLElement>(".trans-row.active");
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selectedSegment, alignment, textid, seq]);

  // Consume pendingHighlight (e.g. annotation-card click) by scrolling the
  // matching source span into view and flashing it. Mirrors TextViewer.
  useLayoutEffect(() => {
    if (
      pending == null ||
      pending.textid !== textid ||
      pending.seq !== seq ||
      pending.bucket !== "body" ||
      containerRef.current == null ||
      alignment == null
    ) {
      return;
    }
    if (lastFlashedRef.current === pending) return;
    const start = pending.offset;
    const end = pending.offset + Math.max(1, pending.length);
    const target = containerRef.current.querySelector<HTMLElement>(
      `span[data-bucket="body"][data-offset="${start}"]`,
    );
    if (!target) return;
    lastFlashedRef.current = pending;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlashOffsets({ start, end });
    workspace.consumeHighlight();
  }, [pending, textid, seq, alignment]);

  useEffect(() => {
    if (flashOffsets == null) return;
    const timer = window.setTimeout(() => setFlashOffsets(null), 15000);
    return () => window.clearTimeout(timer);
  }, [flashOffsets]);

  const handleAnnClick = useCallback(
    (
      offset: number,
      endOffset: number,
      ch: string,
      row: TranslationAlignedRow,
      anns: Annotation[],
    ) => {
      const anchorId =
        row.source_marker_id && hasKrpLocation(row.source_marker_id)
          ? row.source_marker_id
          : null;
      workspace.setSelection({
        textid,
        seq,
        bucket: "body",
        start: offset,
        end: endOffset,
        chars: [ch],
        anchorMarkerId: anchorId,
        anchorOffset: anchorId ? offset - row.source_offset : offset,
      });
      const targetId = anns.find((a) => a.id != null)?.id ?? null;
      workspace.setSelectedAnnotationId(targetId);
      workspace.setSearchQuery(ch);
      workspace.setRightTab("annotations");
    },
    [textid, seq],
  );

  const handleMouseUp = useCallback(() => {
    if (isResizing()) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) {
      workspace.setSelection(null);
      return;
    }
    if (!containerRef.current) return;
    const range = sel.getRangeAt(0);
    if (!containerRef.current.contains(range.commonAncestorContainer)) return;

    const spans = containerRef.current.querySelectorAll<HTMLElement>("span[data-offset]");
    const offsets: number[] = [];
    const endOffsets: number[] = [];
    const selChars: string[] = [];
    let anchorMarkerId: string | null = null;
    let anchorRowOffset = 0;
    spans.forEach((sp) => {
      if (!range.intersectsNode(sp)) return;
      const off = Number(sp.dataset.offset);
      if (Number.isNaN(off)) return;
      const ch = sp.textContent ?? "";
      const cp = ch.codePointAt(0) ?? 0;
      if (PUNCT_RE.test(ch)) return;
      if (!isCjk(ch) && !isPua(cp)) return;
      if (anchorMarkerId === null) {
        const id = sp.dataset.anchorId ?? null;
        if (id && hasKrpLocation(id)) {
          anchorMarkerId = id;
          anchorRowOffset = Number(sp.dataset.anchorRowOffset ?? "0");
        }
      }
      offsets.push(off);
      const endOff = Number(sp.dataset.endOffset);
      endOffsets.push(Number.isNaN(endOff) ? off + 1 : endOff);
      selChars.push(ch);
    });
    if (offsets.length === 0) {
      workspace.setSelection(null);
      return;
    }
    const start = Math.min(...offsets);
    const end = Math.max(...endOffsets);
    workspace.setSelection({
      textid,
      seq,
      bucket: "body",
      start,
      end,
      chars: selChars,
      anchorMarkerId,
      anchorOffset: start - anchorRowOffset,
    });
    workspace.setSearchQuery(selChars.join(""));
    workspace.setRightTab("annotations");
  }, [textid, seq]);

  // iOS Safari often doesn't deliver mouseup after the selection grippers are
  // dragged, so commit via a debounced selectionchange as well.
  useEffect(() => {
    let timer: number | null = null;
    const onSelChange = () => {
      // Caret movement inside a textarea/input/contenteditable also fires
      // selectionchange; ignore it so typing in the compose box doesn't
      // clear the document selection.
      const ae = document.activeElement as HTMLElement | null;
      if (ae && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT" || ae.isContentEditable)) {
        return;
      }
      if (timer != null) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = null;
        handleMouseUp();
      }, 250);
    };
    document.addEventListener("selectionchange", onSelChange);
    return () => {
      document.removeEventListener("selectionchange", onSelChange);
      if (timer != null) window.clearTimeout(timer);
    };
  }, [handleMouseUp]);

  if (!translationId) {
    if (noTranslationsAvailable) {
      return <div className="empty-pane">No translations available — opening in Read mode…</div>;
    }
    return <div className="empty-pane">Select a translation from Translations.</div>;
  }
  if (error) {
    return <div className="empty-pane">Failed to load translation: {error}</div>;
  }
  if (!alignment) {
    return <div className="empty-pane">Loading translation…</div>;
  }
  if (alignment.status === "no_alignment_markers") {
    return <div className="empty-pane">No source segment markers are available for translation alignment.</div>;
  }

  const title = alignment.translation?.title ?? alignment.translation?.id ?? translationId;
  const lang = alignment.translation?.language;
  const translators = (alignment.translation?.responsibility ?? [])
    .map((r) => r.name)
    .filter((n): n is string => typeof n === "string" && n.length > 0)
    .join(", ");
  const date = alignment.translation?.date;

  const firstTranslatedIdx = alignment.rows.findIndex((r) => r.translation_text && !r.continued);

  return (
    <div
      className="ec"
      onMouseUp={handleMouseUp}
      onMouseLeave={() => workspace.setHover(paneId, tabId, null)}
    >
      <div className="tv-title">
        <h1>{title}</h1>
        <h2>
          {textid} · juan {seq}
          {lang ? ` · ${lang}` : ""}
          {translators ? ` · ${translators}` : ""}
          {date ? ` · ${date.slice(0, 4)}` : ""}
        </h2>
        {firstTranslatedIdx >= 0 && (
          <div className="juan-nav">
            <button
              className="juan-nav-btn"
              onClick={() => {
                containerRef.current
                  ?.querySelector<HTMLElement>("[data-first-translated]")
                  ?.scrollIntoView({ block: "start", behavior: "smooth" });
              }}
            >
              ↓ first translation
            </button>
          </div>
        )}
      </div>
      <div className="trans-grid" ref={containerRef}>
        {alignment.rows.map((row, rowIdx) => {
          const isActive =
            selectedSegment?.textid === textid &&
            selectedSegment.seq === seq &&
            selectedSegment.corresp === row.corresp;
          return (
          <div
            className={`trans-row${row.continued ? " continued" : ""}${isActive ? " active" : ""}`}
            key={`${row.source_marker_id}:${row.source_offset}`}
            {...(rowIdx === firstTranslatedIdx ? { "data-first-translated": "1" } : {})}
          >
            <div
              className="trans-source"
              onClick={() =>
                workspace.setSelectedSegment({
                  textid,
                  seq,
                  corresp: row.corresp,
                  sourceText: row.source_text,
                })
              }
            >
              <div className="trans-ref">
                {row.corresp}
                {row.resp ? <span className="trans-resp"> · {row.resp}</span> : null}
              </div>
              <div>
                <SourceText
                  row={row}
                  paneId={paneId}
                  tabId={tabId}
                  annIndex={annIndex}
                  flashOffsets={flashOffsets}
                  onAnnClick={handleAnnClick}
                />
              </div>
            </div>
            <div className="trans-target">
              {row.translation_text ? (
                row.translation_text.split("\n").map((line, i) => (
                  <p key={i}>{line}</p>
                ))
              ) : (
                <span className="trans-missing">
                  {row.continued ? "continued" : "untranslated"}
                </span>
              )}
            </div>
          </div>
          );
        })}
      </div>
    </div>
  );
}
