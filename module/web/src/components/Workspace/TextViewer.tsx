import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { getAnnotations, getJuan, getManifest } from "../../api/client";
import type { Annotation, Juan, JuanMarker, Manifest } from "../../api/types";
import { decodeKrRefs } from "../../lib/pua";
import {
  isResizing,
  useWorkspace,
  workspace,
  type LineMode,
} from "../../state/useWorkspace";
import { annTooltip, buildAnnotationIndex } from "./AnnotationLayer";

const PUNCT_RE = /[\u3000-\u303F\uFF00-\uFFEF：「」『』，。、！？；…—\s\u00B7]/;
const PHRASE_END_RE = /[。！？；]/;
const CJK_RE = /[\u3400-\u9FFF\uF900-\uFAFF]/;

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

interface RenderedChar {
  ch: string;
  // The original master_offset this char carries; null for chars injected
  // from a punctuation marker (they don't index into annotations).
  srcOffset: number | null;
  isPunct: boolean;
  isNewline: boolean;
}

interface Block {
  // half-open [startOffset, endOffset) over the *original* master text.
  startOffset: number;
  endOffset: number;
  chars: RenderedChar[];
  estimatedHeight: number;
}

const FALLBACK_LINE_HEIGHT = 38;
const FALLBACK_CHARS_PER_LINE = 24;

function estimateBlockHeight(charCount: number): number {
  const lines = Math.max(1, Math.ceil(charCount / FALLBACK_CHARS_PER_LINE));
  return lines * FALLBACK_LINE_HEIGHT;
}

// Build the rendered char stream: decode PUA refs, then inject punctuation
// markers at their master_offset (skipping injection where the master text
// already has punctuation at that position).
function buildRenderedChars(
  bodyText: string,
  markers: JuanMarker[],
): RenderedChar[] {
  const decoded = decodeKrRefs(bodyText);
  const chars = [...decoded];

  type Inject = { offset: number; content: string };
  const injects: Inject[] = [];
  for (const m of markers) {
    if (m.type !== "punctuation") continue;
    const off = typeof m.offset === "number" ? m.offset : 0;
    const raw = m.content;
    if (typeof raw !== "string" || raw.length === 0) continue;
    const here = chars[off];
    if (here && PUNCT_RE.test(here)) continue;
    injects.push({ offset: off, content: raw });
  }
  injects.sort((a, b) => a.offset - b.offset);

  const out: RenderedChar[] = [];
  let injectIdx = 0;
  for (let i = 0; i <= chars.length; i++) {
    while (injectIdx < injects.length && injects[injectIdx].offset === i) {
      for (const ch of [...injects[injectIdx].content]) {
        out.push({ ch, srcOffset: null, isPunct: true, isNewline: false });
      }
      injectIdx++;
    }
    if (i === chars.length) break;
    const ch = chars[i];
    out.push({
      ch,
      srcOffset: i,
      isPunct: PUNCT_RE.test(ch),
      isNewline: ch === "\n",
    });
  }
  return out;
}

// Group rendered chars into blocks. Block boundary policy depends on lineMode:
//   paragraph: split on `paragraph-break` markers; fall back to literal `\n`.
//   phrase:    split on `tls:seg` markers; fall back to phrase-ending punct.
function buildBlocks(
  chars: RenderedChar[],
  markers: JuanMarker[],
  lineMode: LineMode,
  bodyLength: number,
): Block[] {
  // Marker-derived boundary set: master_offset values where a new block starts.
  const markerType = lineMode === "phrase" ? "tls:seg" : "paragraph-break";
  const boundaryOffsets = new Set<number>();
  for (const m of markers) {
    if (m.type !== markerType) continue;
    const off = typeof m.offset === "number" ? m.offset : 0;
    boundaryOffsets.add(off);
  }

  const useMarkers = boundaryOffsets.size > 0;

  const blocks: Block[] = [];
  let cur: RenderedChar[] = [];
  let curStart = 0;
  let curEnd = 0;

  const flush = () => {
    if (cur.length === 0) return;
    blocks.push({
      startOffset: curStart,
      endOffset: curEnd,
      chars: cur,
      estimatedHeight: estimateBlockHeight(cur.length),
    });
    cur = [];
  };

  for (const rc of chars) {
    const nextSrc = rc.srcOffset;
    // Decide whether to start a new block *before* placing this char.
    if (cur.length > 0 && nextSrc != null) {
      if (useMarkers) {
        if (boundaryOffsets.has(nextSrc)) {
          flush();
        }
      } else if (lineMode === "paragraph") {
        // fall-back: literal newline starts a new block (the newline lives
        // at the END of the previous block, not the start of the next).
        // handled below after appending the char.
      }
    }
    if (cur.length === 0) curStart = nextSrc ?? curEnd;
    cur.push(rc);
    if (nextSrc != null) curEnd = nextSrc + 1;

    // Fall-back end-of-block triggers (after appending this char):
    if (!useMarkers) {
      if (lineMode === "paragraph" && rc.isNewline) {
        flush();
      } else if (lineMode === "phrase" && PHRASE_END_RE.test(rc.ch)) {
        flush();
      }
    }
  }
  if (cur.length > 0) {
    if (curEnd === curStart) curEnd = bodyLength;
    flush();
  }
  return blocks;
}

interface Props {
  textid: string;
  seq: number;
}

export function TextViewer({ textid, seq }: Props) {
  const [juan, setJuan] = useState<Juan | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pending = useWorkspace((s) => s.pendingHighlight);
  const lineMode = useWorkspace((s) => s.readPrefs.lineMode);
  const [flashOffsets, setFlashOffsets] = useState<{ start: number; end: number } | null>(
    null,
  );
  // Identity of the pendingHighlight we have already flashed for, so the
  // layout effect can re-run safely (deps include `blocks` + `visibleBlocks`)
  // without scheduling a second scroll/flash for the same target.
  const lastFlashedRef = useRef<typeof pending>(null);

  useEffect(() => {
    let cancelled = false;
    setJuan(null);
    setAnnotations(null);
    setError(null);
    Promise.all([
      getJuan(textid, seq),
      getAnnotations(textid, seq),
      getManifest(textid).catch(() => null),
    ])
      .then(([j, a, m]) => {
        if (cancelled) return;
        setJuan(j);
        setAnnotations(a);
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

  const markers = useMemo<JuanMarker[]>(
    () => (juan?.body?.markers ?? []) as JuanMarker[],
    [juan],
  );

  // Sorted list of id-bearing markers, used to resolve a selection's
  // anchorMarkerId via binary search.
  const idMarkers = useMemo(() => {
    const list: { offset: number; id: string }[] = [];
    for (const m of markers) {
      const id = typeof m.id === "string" ? m.id.trim() : "";
      if (!id) continue;
      const off = typeof m.offset === "number" ? m.offset : 0;
      list.push({ offset: off, id });
    }
    list.sort((a, b) => a.offset - b.offset);
    return list;
  }, [markers]);

  const renderedChars = useMemo(
    () => (juan?.body?.text ? buildRenderedChars(juan.body.text, markers) : []),
    [juan, markers],
  );

  const blocks = useMemo(
    () =>
      buildBlocks(
        renderedChars,
        markers,
        lineMode,
        juan?.body?.text ? [...decodeKrRefs(juan.body.text)].length : 0,
      ),
    [renderedChars, markers, lineMode, juan],
  );

  const annIndex = useMemo(
    () => buildAnnotationIndex(annotations ?? []),
    [annotations],
  );

  // Lazy-mount: visibility flips true once a block enters the viewport (or
  // is force-mounted by pendingHighlight). Once true it stays true so scroll
  // position never jumps.
  const [visibleBlocks, setVisibleBlocks] = useState<Set<number>>(() => new Set());

  // Reset visibility state on key change (new juan / new line-mode triggers
  // a new blocks identity).
  useEffect(() => {
    setVisibleBlocks(new Set([0]));
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [textid, seq, lineMode]);

  // Mount IntersectionObserver after blocks render.
  useEffect(() => {
    const root = scrollRef.current;
    const container = containerRef.current;
    if (!root || !container || blocks.length === 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const newly: number[] = [];
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const idxStr = (e.target as HTMLElement).dataset.blockIdx;
          if (idxStr == null) continue;
          newly.push(Number(idxStr));
        }
        if (newly.length === 0) return;
        setVisibleBlocks((prev) => {
          let changed = false;
          const next = new Set(prev);
          for (const i of newly) {
            if (!next.has(i)) {
              next.add(i);
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      },
      { root, rootMargin: "200% 0px" },
    );
    const placeholders = container.querySelectorAll<HTMLElement>("[data-block-idx]");
    placeholders.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [blocks]);

  // Consume pendingHighlight from a search-result click: ensure the block
  // containing the target offset is mounted, then scroll + flash on next paint.
  useEffect(() => {
    if (
      pending == null ||
      pending.textid !== textid ||
      pending.seq !== seq ||
      blocks.length === 0
    ) {
      return;
    }
    const targetIdx = blocks.findIndex(
      (b) => pending.offset >= b.startOffset && pending.offset < b.endOffset,
    );
    if (targetIdx < 0) {
      workspace.consumeHighlight();
      return;
    }
    setVisibleBlocks((prev) => {
      if (prev.has(targetIdx)) return prev;
      const next = new Set(prev);
      next.add(targetIdx);
      return next;
    });
  }, [pending, textid, seq, blocks]);

  useLayoutEffect(() => {
    if (
      pending == null ||
      pending.textid !== textid ||
      pending.seq !== seq ||
      containerRef.current == null
    ) {
      return;
    }
    // Already flashed for this exact pending object — subsequent re-runs
    // (e.g. visibleBlocks update from IntersectionObserver after scroll)
    // must not re-trigger scroll/flash.
    if (lastFlashedRef.current === pending) return;
    const start = pending.offset;
    const end = pending.offset + Math.max(1, pending.length);
    const target = containerRef.current.querySelector<HTMLElement>(
      `span[data-offset="${start}"]`,
    );
    if (!target) return;
    lastFlashedRef.current = pending;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlashOffsets({ start, end });
    workspace.consumeHighlight();
  }, [pending, textid, seq, visibleBlocks, blocks]);

  // Clear the flash after a delay. Decoupled from the flash-set effect so
  // its cleanup can't nuke the timer when unrelated deps change.
  useEffect(() => {
    if (flashOffsets == null) return;
    const timer = window.setTimeout(() => setFlashOffsets(null), 15000);
    return () => window.clearTimeout(timer);
  }, [flashOffsets]);

  const resolveAnchor = useCallback(
    (offset: number): { anchorMarkerId: string | null; anchorOffset: number } => {
      // Largest idMarkers entry with offset <= the selection start.
      let lo = 0;
      let hi = idMarkers.length - 1;
      let bestIdx = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (idMarkers[mid].offset <= offset) {
          bestIdx = mid;
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }
      if (bestIdx < 0) return { anchorMarkerId: null, anchorOffset: offset };
      const m = idMarkers[bestIdx];
      return { anchorMarkerId: m.id, anchorOffset: offset - m.offset };
    },
    [idMarkers],
  );

  const handleMouseUp = useCallback(() => {
    // Drag-end of a panel resize bubbles a mouseup into .ec; skip the
    // entire selection-commit path so it can't hijack the right-tab focus.
    if (isResizing()) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) {
      workspace.setSelection(null);
      return;
    }
    if (!containerRef.current) return;
    const range = sel.getRangeAt(0);
    if (!containerRef.current.contains(range.commonAncestorContainer)) return;

    const spans = containerRef.current.querySelectorAll<HTMLElement>(
      "span[data-offset]",
    );
    const offsets: number[] = [];
    const selChars: string[] = [];
    spans.forEach((sp) => {
      if (!range.intersectsNode(sp)) return;
      const off = Number(sp.dataset.offset);
      if (Number.isNaN(off)) return;
      const ch = sp.textContent ?? "";
      const cp = ch.codePointAt(0) ?? 0;
      if (PUNCT_RE.test(ch)) return;
      if (!isCjk(ch) && !isPua(cp)) return;
      offsets.push(off);
      selChars.push(ch);
    });
    if (offsets.length === 0) {
      workspace.setSelection(null);
      return;
    }
    const start = Math.min(...offsets);
    const end = Math.max(...offsets) + 1;
    const anchor = resolveAnchor(start);
    workspace.setSelection({
      textid,
      seq,
      start,
      end,
      chars: selChars,
      ...anchor,
    });
    workspace.setSearchQuery(selChars.join(""));
    workspace.setRightTab("annotations");
  }, [textid, seq, resolveAnchor]);

  if (error) {
    return <div className="empty-pane">Failed to load: {error}</div>;
  }
  if (!juan || !annotations) {
    return <div className="empty-pane">Loading juan {seq}…</div>;
  }

  const title = manifest?.metadata?.title ?? textid;
  const editionShort = manifest?.metadata?.edition?.short ?? null;

  return (
    <div
      className="ec"
      ref={scrollRef}
      onMouseUp={handleMouseUp}
      onMouseLeave={() => workspace.setHover(null)}
    >
      <div className="tv-title">
        <h1>{title}</h1>
        <h2>
          {textid}
          {editionShort ? ` · ${editionShort}` : ""} · juan {seq}
        </h2>
      </div>
      <div className={`tv-body tv-body-${lineMode}`} ref={containerRef}>
        {blocks.map((b, idx) => (
          <BlockView
            key={idx}
            blockIdx={idx}
            block={b}
            visible={visibleBlocks.has(idx)}
            annIndex={annIndex}
            flashOffsets={flashOffsets}
            textid={textid}
            seq={seq}
            resolveAnchor={resolveAnchor}
          />
        ))}
      </div>
    </div>
  );
}

interface BlockViewProps {
  blockIdx: number;
  block: Block;
  visible: boolean;
  annIndex: ReturnType<typeof buildAnnotationIndex>;
  flashOffsets: { start: number; end: number } | null;
  textid: string;
  seq: number;
  resolveAnchor: (
    offset: number,
  ) => { anchorMarkerId: string | null; anchorOffset: number };
}

function BlockView({
  blockIdx,
  block,
  visible,
  annIndex,
  flashOffsets,
  textid,
  seq,
  resolveAnchor,
}: BlockViewProps) {
  if (!visible) {
    return (
      <div
        className="tv-block tv-block-placeholder"
        data-block-idx={blockIdx}
        style={{ minHeight: block.estimatedHeight }}
      />
    );
  }
  return (
    <div
      className="tv-block"
      data-block-idx={blockIdx}
      data-block-start={block.startOffset}
      data-block-end={block.endOffset}
    >
      {block.chars.map((rc, i) => {
        if (rc.isNewline) return <br key={i} />;
        if (rc.isPunct) {
          // Injected punctuation has no srcOffset; existing punct still has one
          // but we don't expose it for selection.
          return (
            <span key={i} className="pu">
              {rc.ch}
            </span>
          );
        }
        const off = rc.srcOffset!;
        const anns = annIndex.byOffset.get(off);
        const has = anns && anns.length > 0;
        const flashing =
          flashOffsets != null && off >= flashOffsets.start && off < flashOffsets.end;
        const cls = `${has ? "ch has-ann" : "ch"}${flashing ? " kwic-flash" : ""}`;
        const title = has ? anns!.map(annTooltip).join(" / ") : undefined;
        return (
          <span
            key={i}
            className={cls}
            data-offset={off}
            title={title}
            onMouseEnter={() => workspace.setHover(rc.ch)}
            onClick={(ev) => {
              if (!has) return;
              // Suppress when this click is part of a drag-selection — let
              // mouseUp's getSelection() path handle multi-char selections.
              const sel = window.getSelection();
              if (sel && !sel.isCollapsed) return;
              const anchor = resolveAnchor(off);
              workspace.setSelection({
                textid,
                seq,
                start: off,
                end: off + 1,
                chars: [rc.ch],
                ...anchor,
              });
              workspace.setSearchQuery(rc.ch);
              workspace.setRightTab("annotations");
              ev.stopPropagation();
            }}
          >
            {rc.ch}
          </span>
        );
      })}
    </div>
  );
}
