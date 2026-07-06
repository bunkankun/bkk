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
  getCatalog,
  getJuan,
  getJuanParallelsStatus,
  getManifest,
} from "../../api/client";
import type {
  Annotation,
  CatalogMatch,
  Juan,
  JuanMarker,
  Manifest,
} from "../../api/types";
import { krRefToChar } from "../../lib/pua";
import {
  isResizing,
  useWorkspace,
  workspace,
  type LineBreakDisplay,
  type LineMode,
} from "../../state/useWorkspace";
import { parseMarkerId, hasKrpLocation } from "../../lib/markers";
import { annTooltip, buildAnnotationIndex } from "./AnnotationLayer";

const PUNCT_RE = /[\u3000-\u303F\uFF00-\uFFEF：「」『』，。、！？；…—\s\u00B7]/;
const PHRASE_END_RE = /[。！？；，：]/;
const PHRASE_LINE_OPENER_RE = /^[「『《〈（(〔【]$/;
const PHRASE_LINE_CLOSER_RE = /^[」]$/;
const CJK_RE = /[\u3400-\u9FFF\uF900-\uFAFF]/;
const BUCKETS = ["front", "body", "back"] as const;

type BucketName = (typeof BUCKETS)[number];

function isBucketName(value: unknown): value is BucketName {
  return typeof value === "string" && (BUCKETS as readonly string[]).includes(value);
}

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
  // Half-open end offset in the original master text. This differs from
  // srcOffset + 1 when a rendered PUA char comes from an `&KRnnn;` ref.
  srcEndOffset: number | null;
  isPunct: boolean;
  isNewline: boolean;
  // Zero-width anchor injected at a page-break marker's offset; observed by
  // the page-anchor IntersectionObserver to drive Inspect-mode's ImagePanel.
  pageAnchor?: { id: string; offset: number };
}

interface Block {
  bucket: BucketName;
  // half-open [startOffset, endOffset) over the *original* master text.
  startOffset: number;
  endOffset: number;
  chars: RenderedChar[];
  estimatedHeight: number;
  tagName: "div" | "p";
}

const FALLBACK_LINE_HEIGHT = 38;
const FALLBACK_CHARS_PER_LINE = 24;
const KR_REF_START_RE = /^&KR(\d+);/;
const BUNKANKUN_BASE_URL = "https://ask.bunkankun.org";

interface SourceChar {
  ch: string;
  srcOffset: number;
  srcEndOffset: number;
}

function decodeKrRefsWithOffsets(text: string): SourceChar[] {
  const rawChars = [...text];
  const out: SourceChar[] = [];
  let i = 0;
  while (i < rawChars.length) {
    const rest = rawChars.slice(i, i + 16).join("");
    const match = rest.match(KR_REF_START_RE);
    if (match) {
      out.push({
        ch: krRefToChar(match[1]),
        srcOffset: i,
        srcEndOffset: i + [...match[0]].length,
      });
      i += [...match[0]].length;
      continue;
    }
    out.push({ ch: rawChars[i], srcOffset: i, srcEndOffset: i + 1 });
    i++;
  }
  return out;
}

function isParagraphBoundaryMarker(m: JuanMarker): boolean {
  if (m.type === "paragraph-break") {
    return m.role === "open" || m.role == null;
  }
  if (m.type !== "xml-element") return false;
  const name = typeof m.name === "string" ? m.name : "";
  return (name === "p" || name.endsWith(":p")) && m.role === "open";
}

function estimateBlockHeight(chars: RenderedChar[]): number {
  let visible = 0;
  for (const c of chars) {
    if (c.pageAnchor) continue;
    visible++;
  }
  if (visible === 0) return 0;
  const lines = Math.max(1, Math.ceil(visible / FALLBACK_CHARS_PER_LINE));
  return lines * FALLBACK_LINE_HEIGHT;
}

function firstOffset(chars: RenderedChar[], fallback: number): number {
  for (const c of chars) {
    if (c.srcOffset != null) return c.srcOffset;
    if (c.pageAnchor) return c.pageAnchor.offset;
  }
  return fallback;
}

function lastEndOffset(chars: RenderedChar[], fallback: number): number {
  for (let i = chars.length - 1; i >= 0; i--) {
    const c = chars[i];
    if (c.srcEndOffset != null) return c.srcEndOffset;
    if (c.pageAnchor) return c.pageAnchor.offset;
  }
  return fallback;
}

// Build the rendered char stream: decode PUA refs, then inject punctuation
// markers at their master_offset (skipping injection where the master text
// already has punctuation at that position). Page-break markers are also
// injected here as zero-width anchor entries so the Inspect-mode observer
// can track which page the user is currently reading.
function buildRenderedChars(
  bodyText: string,
  markers: JuanMarker[],
): RenderedChar[] {
  const chars = decodeKrRefsWithOffsets(bodyText);
  const bodyLength = [...bodyText].length;
  const charAtOffset = new Map(chars.map((c) => [c.srcOffset, c.ch]));

  type PunctInject = { offset: number; content: string };
  type PageInject = { offset: number; id: string };
  const punctInjects: PunctInject[] = [];
  const pageInjects: PageInject[] = [];
  for (const m of markers) {
    const off = typeof m.offset === "number" ? m.offset : 0;
    if (m.type === "punctuation") {
      const raw = m.content;
      if (typeof raw !== "string" || raw.length === 0) continue;
      const here = charAtOffset.get(off);
      if (here && PUNCT_RE.test(here)) continue;
      punctInjects.push({ offset: off, content: raw });
    } else if (m.type === "page-break") {
      const id = typeof m.id === "string" ? m.id : "";
      if (!id) continue;
      pageInjects.push({ offset: off, id });
    }
  }
  punctInjects.sort((a, b) => a.offset - b.offset);
  pageInjects.sort((a, b) => a.offset - b.offset);

  const out: RenderedChar[] = [];
  let punctIdx = 0;
  let pageIdx = 0;
  let charIdx = 0;
  for (let i = 0; i <= bodyLength; i++) {
    // Page anchors first so they appear at the top of any cluster of
    // injections — natural reading order makes "current page" track the
    // newly-entered page before its punctuation/text.
    while (pageIdx < pageInjects.length && pageInjects[pageIdx].offset === i) {
      const p = pageInjects[pageIdx];
      out.push({
        ch: "",
        srcOffset: null,
        srcEndOffset: null,
        isPunct: false,
        isNewline: false,
        pageAnchor: { id: p.id, offset: p.offset },
      });
      pageIdx++;
    }
    while (punctIdx < punctInjects.length && punctInjects[punctIdx].offset === i) {
      for (const ch of [...punctInjects[punctIdx].content]) {
        out.push({
          ch,
          srcOffset: null,
          srcEndOffset: null,
          isPunct: true,
          isNewline: false,
        });
      }
      punctIdx++;
    }
    while (charIdx < chars.length && chars[charIdx].srcOffset === i) {
      const c = chars[charIdx];
      out.push({
        ch: c.ch,
        srcOffset: c.srcOffset,
        srcEndOffset: c.srcEndOffset,
        isPunct: PUNCT_RE.test(c.ch),
        isNewline: c.ch === "\n",
      });
      charIdx++;
    }
  }
  return out;
}

// Group rendered chars into blocks. Block boundary policy depends on lineMode:
//   paragraph: split on paragraph starts (`paragraph-break` or xml <p> open
//              markers); fall back to literal `\n`.
//   phrase:    split on `tls:seg` markers; fall back to phrase-ending punct.
function buildBlocks(
  bucket: BucketName,
  chars: RenderedChar[],
  markers: JuanMarker[],
  lineMode: LineMode,
  bodyLength: number,
): Block[] {
  // Marker-derived boundary set: master_offset values where a new block starts.
  const boundaryOffsets = new Set<number>();
  for (const m of markers) {
    const isBoundary =
      lineMode === "phrase" ? m.type === "tls:seg" : isParagraphBoundaryMarker(m);
    if (!isBoundary) continue;
    const off = typeof m.offset === "number" ? m.offset : 0;
    boundaryOffsets.add(off);
  }

  const useMarkers = boundaryOffsets.size > 0;

  const blocks: Block[] = [];
  let cur: RenderedChar[] = [];
  let lastEnd = 0;
  let pendingPhraseBreak = false;

  const flush = () => {
    if (cur.length === 0) return;
    let charsForBlock = cur;
    let carry: RenderedChar[] = [];
    if (lineMode === "phrase") {
      let carryStart = cur.length;
      while (
        carryStart > 0 &&
        !cur[carryStart - 1].pageAnchor &&
        PHRASE_LINE_OPENER_RE.test(cur[carryStart - 1].ch)
      ) {
        carryStart--;
      }
      if (carryStart > 0 && carryStart < cur.length) {
        charsForBlock = cur.slice(0, carryStart);
        carry = cur.slice(carryStart);
      }
    }
    const startOffset = firstOffset(charsForBlock, lastEnd);
    const endOffset = lastEndOffset(charsForBlock, startOffset);
    blocks.push({
      bucket,
      startOffset,
      endOffset,
      chars: charsForBlock,
      estimatedHeight: estimateBlockHeight(charsForBlock),
      tagName: lineMode === "paragraph" ? "p" : "div",
    });
    lastEnd = endOffset;
    cur = carry;
  };

  for (const rc of chars) {
    const nextSrc = rc.srcOffset;
    // Decide whether to start a new block *before* placing this char.
    if (cur.length > 0 && nextSrc != null) {
      if (useMarkers) {
        if (boundaryOffsets.has(nextSrc) && !PHRASE_LINE_CLOSER_RE.test(rc.ch)) {
          flush();
        }
      } else if (lineMode === "paragraph") {
        // fall-back: literal newline starts a new block (the newline lives
        // at the END of the previous block, not the start of the next).
        // handled below after appending the char.
      } else if (
        lineMode === "phrase" &&
        pendingPhraseBreak &&
        !PHRASE_LINE_CLOSER_RE.test(rc.ch)
      ) {
        pendingPhraseBreak = false;
        flush();
      }
    }
    cur.push(rc);
    if (rc.srcEndOffset != null) lastEnd = rc.srcEndOffset;

    // Fall-back end-of-block triggers (after appending this char):
    if (!useMarkers) {
      if (lineMode === "paragraph" && rc.isNewline) {
        flush();
      } else if (lineMode === "phrase" && PHRASE_END_RE.test(rc.ch)) {
        pendingPhraseBreak = true;
      }
    }
  }
  if (cur.length > 0) {
    flush();
  }
  if (blocks.length > 0) {
    const last = blocks[blocks.length - 1];
    if (last.endOffset === last.startOffset) last.endOffset = bodyLength;
  }
  return blocks;
}

function hasBucketText(juan: Juan, bucket: BucketName): boolean {
  const text = juan[bucket]?.text;
  return typeof text === "string" && text.length > 0;
}

function firstPageMarker(
  juan: Juan,
): { bucket: BucketName; marker: JuanMarker } | null {
  for (const bucket of BUCKETS) {
    const marker = ((juan[bucket]?.markers ?? []) as JuanMarker[]).find(
      (mk) => mk.type === "page-break" && typeof mk.id === "string",
    );
    if (marker) return { bucket, marker };
  }
  return null;
}

function textidToBunkankunUrl(textid: string): string {
  return `${BUNKANKUN_BASE_URL}/${textid.slice(0, 3)}/${textid.slice(
    0,
    4,
  )}/${textid}`;
}

function catalogString(match: CatalogMatch | null, key: string): string | null {
  const value = match?.metadata?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function manifestAltIds(manifest: Manifest | null): string[] {
  const identifiers = (manifest?.metadata as { identifiers?: unknown } | undefined)
    ?.identifiers;
  if (!identifiers || typeof identifiers !== "object") return [];
  const raw = (identifiers as { alt_id?: unknown }).alt_id;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((v) => (typeof v === "string" ? v.trim() : ""))
    .filter((v) => v.length > 0);
}

interface Props {
  paneId: string;
  tabId: string;
  textid: string;
  seq: number;
  lineMode: LineMode;
}

export function TextViewer({ paneId, tabId, textid, seq, lineMode }: Props) {
  const rightTab = useWorkspace((s) => s.rightTab);
  const showPageBreaks = useWorkspace((s) => s.readPrefs.showPageBreaks);
  const lineBreakDisplay = useWorkspace((s) => s.readPrefs.lineBreakDisplay);
  const currentPageMarkerId = useWorkspace((s) =>
    s.currentPage && s.currentPage.textid === textid && s.currentPage.seq === seq
      ? s.currentPage.markerId
      : null,
  );
  const activeEdition = useMemo(
    () => (currentPageMarkerId ? parseMarkerId(currentPageMarkerId)?.edition ?? null : null),
    [currentPageMarkerId],
  );
  const [juan, setJuan] = useState<Juan | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [catalogMatch, setCatalogMatch] = useState<CatalogMatch | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[] | null>(null);
  const [hasParallels, setHasParallels] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pending = useWorkspace((s) => s.pendingHighlight);
  const [flashOffsets, setFlashOffsets] = useState<{ start: number; end: number } | null>(
    null,
  );
  const [flashBucket, setFlashBucket] = useState<BucketName | null>(null);
  // Identity of the pendingHighlight we have already flashed for, so the
  // layout effect can re-run safely (deps include `blocks` + `visibleBlocks`)
  // without scheduling a second scroll/flash for the same target.
  const lastFlashedRef = useRef<typeof pending>(null);
  const lastScrollSyncRef = useRef<string>("");

  useEffect(() => {
    let cancelled = false;
    setJuan(null);
    setAnnotations(null);
    setCatalogMatch(null);
    setHasParallels(null);
    setError(null);
    Promise.all([
      getJuan(textid, seq),
      getAnnotations(textid, seq),
      getManifest(textid).catch(() => null),
      getCatalog({ q: textid, limit: 10 }).catch(() => null),
      getJuanParallelsStatus(textid, seq).catch(() => null),
    ])
      .then(([j, a, m, catalog, parallels]) => {
        if (cancelled) return;
        setJuan(j);
        setAnnotations(a);
        setManifest(m);
        setHasParallels(parallels?.has_parallels ?? null);
        setCatalogMatch(
          catalog?.matches.find((match) => match.textid === textid) ?? null,
        );
        // Seed currentPage to the juan's first page-break so the image panel
        // has something to show before the user scrolls.
        const first = firstPageMarker(j);
        if (first && typeof first.marker.id === "string") {
          workspace.setCurrentPage({
            textid,
            seq,
            bucket: first.bucket,
            markerId: first.marker.id,
            offset: typeof first.marker.offset === "number" ? first.marker.offset : 0,
          });
        }
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
    const onChanged = (event: Event) => {
      const detail = (event as CustomEvent<{
        textid?: string;
        seq?: number;
        hasParallels?: boolean;
      }>).detail;
      if (detail?.textid === textid && detail.seq === seq) {
        setHasParallels(detail.hasParallels === true);
      }
    };
    window.addEventListener("bkk:juan-parallels-changed", onChanged);
    return () => window.removeEventListener("bkk:juan-parallels-changed", onChanged);
  }, [textid, seq]);

  const bucketViews = useMemo(
    () =>
      juan
        ? BUCKETS.filter((bucket) => hasBucketText(juan, bucket)).map((bucket) => ({
            bucket,
            text: juan[bucket]?.text ?? "",
            markers: (juan[bucket]?.markers ?? []) as JuanMarker[],
          }))
        : [],
    [juan],
  );

  // Sorted list of id-bearing markers, used to resolve a selection's
  // anchorMarkerId via binary search.
  const idMarkers = useMemo(() => {
    const byBucket = new Map<BucketName, { offset: number; id: string }[]>();
    for (const view of bucketViews) {
      const list: { offset: number; id: string }[] = [];
      for (const m of view.markers) {
        const id = typeof m.id === "string" ? m.id.trim() : "";
        if (!id) continue;
        const off = typeof m.offset === "number" ? m.offset : 0;
        list.push({ offset: off, id });
      }
      list.sort((a, b) => a.offset - b.offset);
      byBucket.set(view.bucket, list);
    }
    return byBucket;
  }, [bucketViews]);

  const blocksByBucket = useMemo(
    () =>
      bucketViews.map((view) => ({
        bucket: view.bucket,
        blocks: buildBlocks(
          view.bucket,
          buildRenderedChars(view.text, view.markers),
          view.markers,
          lineMode,
          [...view.text].length,
        ),
      })),
    [bucketViews, lineMode],
  );

  const blocks = useMemo(
    () => blocksByBucket.flatMap((view) => view.blocks),
    [blocksByBucket],
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
    workspace.setCurrentPage(null);
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

  // Emit the source offset nearest the top of this pane so the translation
  // sidecar can follow manual source scrolling. This is intentionally DOM
  // scoped by pane/tab to keep split panes independent.
  useEffect(() => {
    const root = scrollRef.current;
    const container = containerRef.current;
    if (!root || !container || blocks.length === 0) return;
    let raf = 0;
    const emit = () => {
      raf = 0;
      const rootRect = root.getBoundingClientRect();
      const anchorY = rootRect.top + Math.min(80, Math.max(16, rootRect.height * 0.12));
      const spans = container.querySelectorAll<HTMLElement>("span[data-offset][data-bucket]");
      let best: { bucket: string; offset: number; distance: number } | null = null;
      for (const sp of spans) {
        const bucket = sp.dataset.bucket;
        const offset = Number(sp.dataset.offset);
        if (!bucket || Number.isNaN(offset)) continue;
        const rect = sp.getBoundingClientRect();
        if (rect.bottom < rootRect.top || rect.top > rootRect.bottom) continue;
        const distance =
          rect.top <= anchorY && rect.bottom >= anchorY
            ? 0
            : Math.min(Math.abs(rect.top - anchorY), Math.abs(rect.bottom - anchorY));
        if (best == null || distance < best.distance) {
          best = { bucket, offset, distance };
        }
      }
      if (best == null) return;
      const key = `${best.bucket}:${best.offset}`;
      if (key === lastScrollSyncRef.current) return;
      lastScrollSyncRef.current = key;
      window.dispatchEvent(new CustomEvent("bkk:source-scroll-sync", {
        detail: {
          paneId,
          tabId,
          textid,
          seq,
          bucket: best.bucket,
          offset: best.offset,
        },
      }));
    };
    const schedule = () => {
      if (raf) return;
      raf = window.requestAnimationFrame(emit);
    };
    root.addEventListener("scroll", schedule, { passive: true });
    schedule();
    return () => {
      root.removeEventListener("scroll", schedule);
      if (raf) window.cancelAnimationFrame(raf);
    };
  }, [blocks, visibleBlocks, paneId, tabId, textid, seq]);

  // Page-anchor observer: tracks the topmost page-break anchor in the upper
  // ~15% of the scroll viewport and reports it as currentPage. Distinct from
  // the block observer above (which uses a generous rootMargin for lazy
  // mounting) — entangling the two would force a single rootMargin policy.
  useEffect(() => {
    const root = scrollRef.current;
    const container = containerRef.current;
    if (!root || !container) return;
    const visible = new Map<Element, { id: string; offset: number; bucket: BucketName }>();
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const el = e.target as HTMLElement;
          if (e.isIntersecting) {
            const id = el.dataset.pageId;
            const off = Number(el.dataset.pageOffset);
            const bucket = el.dataset.bucket;
            if (!id || Number.isNaN(off) || !isBucketName(bucket)) continue;
            visible.set(el, { id, offset: off, bucket });
          } else {
            visible.delete(el);
          }
        }
        let best: { id: string; offset: number; bucket: BucketName } | null = null;
        for (const v of visible.values()) {
          if (
            best == null ||
            BUCKETS.indexOf(v.bucket) < BUCKETS.indexOf(best.bucket) ||
            (v.bucket === best.bucket && v.offset < best.offset)
          ) {
            best = v;
          }
        }
        if (best == null) return;
        workspace.setCurrentPage({
          textid,
          seq,
          bucket: best.bucket,
          markerId: best.id,
          offset: best.offset,
        });
      },
      { root, rootMargin: "0px 0px -85% 0px" },
    );
    const anchors = container.querySelectorAll<HTMLElement>(".page-anchor");
    anchors.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [blocks, visibleBlocks, textid, seq]);

  // Consume pendingHighlight from a search-result click: ensure the target
  // block and everything before it are mounted, then scroll + flash on next
  // paint. Punctuation/page markers can make rough placeholder heights drift
  // from real text height; mounting the prefix keeps jump layout stable.
  useEffect(() => {
    if (
      pending == null ||
      pending.textid !== textid ||
      pending.seq !== seq ||
      !isBucketName(pending.bucket) ||
      blocks.length === 0
    ) {
      return;
    }
    const targetIdx = blocks.findIndex(
      (b) =>
        b.bucket === pending.bucket &&
        pending.offset >= b.startOffset &&
        pending.offset < b.endOffset,
    );
    if (targetIdx < 0) {
      workspace.consumeHighlight();
      return;
    }
    setVisibleBlocks((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (let i = 0; i <= targetIdx; i++) {
        if (!next.has(i)) {
          next.add(i);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [pending, textid, seq, blocks]);

  useLayoutEffect(() => {
    if (
      pending == null ||
      pending.textid !== textid ||
      pending.seq !== seq ||
      !isBucketName(pending.bucket) ||
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
      `span[data-bucket="${pending.bucket}"][data-offset="${start}"]`,
    );
    if (!target) return;
    lastFlashedRef.current = pending;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    if (pending.flash !== false) {
      setFlashBucket(pending.bucket);
      setFlashOffsets({ start, end });
    }
    workspace.consumeHighlight();
  }, [pending, textid, seq, visibleBlocks, blocks]);

  // Clear the flash after a delay. Decoupled from the flash-set effect so
  // its cleanup can't nuke the timer when unrelated deps change.
  useEffect(() => {
    if (flashOffsets == null) return;
    const timer = window.setTimeout(() => {
      setFlashOffsets(null);
      setFlashBucket(null);
    }, 15000);
    return () => window.clearTimeout(timer);
  }, [flashOffsets]);

  const resolveAnchor = useCallback(
    (
      bucket: BucketName,
      offset: number,
    ): { anchorMarkerId: string | null; anchorOffset: number } => {
      // Largest idMarkers entry with offset <= the selection start.
      const markers = idMarkers.get(bucket) ?? [];
      let lo = 0;
      let hi = markers.length - 1;
      let bestIdx = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (markers[mid].offset <= offset) {
          bestIdx = mid;
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }
      for (let i = bestIdx; i >= 0; i--) {
        if (hasKrpLocation(markers[i].id)) {
          const m = markers[i];
          return { anchorMarkerId: m.id, anchorOffset: offset - m.offset };
        }
      }
      return { anchorMarkerId: null, anchorOffset: offset };
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

    const spans = containerRef.current.querySelectorAll<HTMLElement>("span[data-offset]");
    let bucket: BucketName | null = null;
    const offsets: number[] = [];
    const endOffsets: number[] = [];
    const selChars: string[] = [];
    spans.forEach((sp) => {
      if (!range.intersectsNode(sp)) return;
      const spanBucket = sp.dataset.bucket;
      if (!isBucketName(spanBucket)) return;
      if (bucket == null) bucket = spanBucket;
      if (spanBucket !== bucket) return;
      const off = Number(sp.dataset.offset);
      if (Number.isNaN(off)) return;
      const ch = sp.textContent ?? "";
      const cp = ch.codePointAt(0) ?? 0;
      if (PUNCT_RE.test(ch)) return;
      if (!isCjk(ch) && !isPua(cp)) return;
      offsets.push(off);
      const endOff = Number(sp.dataset.endOffset);
      endOffsets.push(Number.isNaN(endOff) ? off + 1 : endOff);
      selChars.push(ch);
    });
    if (offsets.length === 0) {
      workspace.setSelection(null);
      return;
    }
    if (bucket == null) return;
    const start = Math.min(...offsets);
    const end = Math.max(...endOffsets);
    const anchor = resolveAnchor(bucket, start);
    workspace.setSelection({
      textid,
      seq,
      bucket,
      start,
      end,
      chars: selChars,
      ...anchor,
    });
    workspace.setSearchQuery(selChars.join(""));
    if (rightTab !== "parallels") workspace.setRightTab("annotations");
  }, [textid, seq, resolveAnchor, rightTab]);

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

  if (error) {
    return <div className="empty-pane">Failed to load: {error}</div>;
  }
  if (!juan || !annotations) {
    return <div className="empty-pane">Loading 卷 {seq}…</div>;
  }

  const title = manifest?.metadata?.title ?? textid;
  const titlePinyin = catalogString(catalogMatch, "title_pinyin");
  const titleEnglish = catalogString(catalogMatch, "title_english");
  const editionShort = manifest?.metadata?.edition?.short ?? null;
  const bunkankunUrl = textidToBunkankunUrl(textid);
  const altIds = manifestAltIds(manifest);

  return (
    <div
      className="ec"
      data-pane-id={paneId}
      data-tab-id={tabId}
      ref={scrollRef}
      onMouseUp={handleMouseUp}
      onMouseLeave={() => workspace.setHover(paneId, tabId, null)}
    >
      <div className="tv-title">
        <h1>{title}</h1>
        {titlePinyin ? <div className="tv-title-pinyin">{titlePinyin}</div> : null}
        {titleEnglish ? (
          <div className="tv-title-english">{titleEnglish}</div>
        ) : null}
        <h2>
          <a href={bunkankunUrl} target="ask-bkk">
            {textid}
          </a>
          {editionShort ? ` · ${editionShort}` : ""} · 卷 {seq}
          <button
            type="button"
            className={`tv-parallels-btn ${
              hasParallels === true
                ? "has-parallels"
                : hasParallels === false
                  ? "no-parallels"
                  : "unknown"
            }`}
            title={
              hasParallels === false
                ? "No stored parallels; find them on demand"
                : "Load parallel passages for this juan"
            }
            onClick={() => workspace.openParallelsPanel(textid, seq)}
          >
            {hasParallels === false ? "Find parallels" : "Parallels"}
          </button>
          {altIds.length > 0 ? (
            <span className="tv-alt-ids"> {altIds.join(" ")}</span>
          ) : null}
        </h2>
      </div>
      <div
        className={`tv-body tv-body-${lineMode}${showPageBreaks ? " tv-show-pb" : ""} tv-lb-${lineBreakDisplay}`}
        ref={containerRef}
      >
        {blocksByBucket.map((view) => (
          <section className={`tv-bucket tv-bucket-${view.bucket}`} key={view.bucket}>
            {view.bucket !== "body" ? (
              <div className="tv-bucket-label">{view.bucket}</div>
            ) : null}
            {view.blocks.map((b) => {
              const idx = blocks.indexOf(b);
              return (
                <BlockView
                  key={`${b.bucket}:${idx}`}
                  blockIdx={idx}
                  block={b}
                  visible={visibleBlocks.has(idx)}
                  annIndex={annIndex}
                  flashOffsets={flashOffsets}
                  flashBucket={flashBucket}
                  textid={textid}
                  seq={seq}
                  paneId={paneId}
                  tabId={tabId}
                  resolveAnchor={resolveAnchor}
                  lineBreakDisplay={lineBreakDisplay}
                  activeEdition={activeEdition}
                />
              );
            })}
          </section>
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
  flashBucket: BucketName | null;
  textid: string;
  seq: number;
  paneId: string;
  tabId: string;
  resolveAnchor: (
    bucket: BucketName,
    offset: number,
  ) => { anchorMarkerId: string | null; anchorOffset: number };
  lineBreakDisplay: LineBreakDisplay;
  activeEdition: string | null;
}

function BlockView({
  blockIdx,
  block,
  visible,
  annIndex,
  flashOffsets,
  flashBucket,
  textid,
  seq,
  paneId,
  tabId,
  resolveAnchor,
  lineBreakDisplay,
  activeEdition,
}: BlockViewProps) {
  const Tag = block.tagName;

  if (!visible) {
    return (
      <Tag
        className="tv-block tv-block-placeholder"
        data-block-idx={blockIdx}
        style={{ minHeight: block.estimatedHeight }}
      />
    );
  }
  return (
    <Tag
      className="tv-block"
      data-block-idx={blockIdx}
      data-block-start={block.startOffset}
      data-block-end={block.endOffset}
    >
      {block.chars.map((rc, i) => {
        if (rc.pageAnchor) {
          const parsed = parseMarkerId(rc.pageAnchor.id);
          const match = activeEdition != null && parsed?.edition === activeEdition;
          return (
            <span
              key={i}
              className={`page-anchor${match ? " page-anchor--match" : ""}`}
              data-bucket={block.bucket}
              data-page-id={rc.pageAnchor.id}
              data-page-offset={rc.pageAnchor.offset}
              title={match ? rc.pageAnchor.id : undefined}
            />
          );
        }
        if (rc.isNewline) {
          if (lineBreakDisplay === "br") return <br key={i} />;
          if (lineBreakDisplay === "glyph")
            return <span key={i} className="lb-mark" aria-hidden="true" />;
          return null;
        }
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
        const has = block.bucket === "body" && anns && anns.length > 0;
        const flashing =
          flashOffsets != null &&
          flashBucket === block.bucket &&
          off >= flashOffsets.start &&
          off < flashOffsets.end;
        const cls = `${has ? "ch has-ann" : "ch"}${flashing ? " kwic-flash" : ""}`;
        const title = has ? anns!.map(annTooltip).join(" / ") : undefined;
        return (
          <span
            key={i}
            className={cls}
            data-bucket={block.bucket}
            data-offset={off}
            data-end-offset={rc.srcEndOffset ?? off + 1}
            title={title}
            onMouseEnter={() => workspace.setHover(paneId, tabId, rc.ch)}
            onClick={(ev) => {
              if (!has) return;
              // Suppress when this click is part of a drag-selection — let
              // mouseUp's getSelection() path handle multi-char selections.
              const sel = window.getSelection();
              if (sel && !sel.isCollapsed) return;
              const anchor = resolveAnchor(block.bucket, off);
              workspace.setSelection({
                textid,
                seq,
                bucket: block.bucket,
                start: off,
                end: off + 1,
                chars: [rc.ch],
                ...anchor,
              });
              const targetId = anns!.find((a) => a.id != null)?.id ?? null;
              workspace.setSelectedAnnotationId(targetId);
              workspace.setSearchQuery(rc.ch);
              workspace.setRightTab("annotations");
              ev.stopPropagation();
            }}
          >
            {rc.ch}
          </span>
        );
      })}
    </Tag>
  );
}
