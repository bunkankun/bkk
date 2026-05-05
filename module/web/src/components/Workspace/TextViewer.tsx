import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAnnotations, getJuan, getManifest } from "../../api/client";
import type { Annotation, Juan, Manifest } from "../../api/types";
import { decodeKrRefs } from "../../lib/pua";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { annTooltip, buildAnnotationIndex } from "./AnnotationLayer";

const PUNCT_RE = /[\u3000-\u303F\uFF00-\uFFEF：「」『』，。、！？；…—\s\u00B7]/;
const CJK_RE = /[\u3400-\u9FFF\uF900-\uFAFF]/;
// PUA ranges (Kanripo lives at 0x105000+, but cover BMP+ supplementary too)
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
  const pending = useWorkspace((s) => s.pendingHighlight);
  const [flashOffsets, setFlashOffsets] = useState<{ start: number; end: number } | null>(
    null,
  );

  // Load juan + annotations + manifest in parallel; reset on key change.
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

  // Decoded body chars (PUA entity refs collapsed to single codepoints).
  const chars = useMemo(() => {
    const raw = juan?.body?.text ?? "";
    const decoded = decodeKrRefs(raw);
    return [...decoded];
  }, [juan]);

  const annIndex = useMemo(
    () => buildAnnotationIndex(annotations ?? []),
    [annotations],
  );

  // Consume pendingHighlight from a search-result click: scroll the
  // master-offset span into view and apply a temporary amber flash.
  useEffect(() => {
    if (
      pending == null ||
      pending.textid !== textid ||
      pending.seq !== seq ||
      juan == null ||
      containerRef.current == null
    ) {
      return;
    }
    const start = pending.offset;
    const end = pending.offset + Math.max(1, pending.length);
    const target = containerRef.current.querySelector<HTMLElement>(
      `span[data-offset="${start}"]`,
    );
    target?.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlashOffsets({ start, end });
    workspace.consumeHighlight();
    const timer = window.setTimeout(() => setFlashOffsets(null), 1200);
    return () => window.clearTimeout(timer);
  }, [pending, textid, seq, juan]);

  const handleMouseUp = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) {
      workspace.setSelection(null);
      return;
    }
    if (!containerRef.current) return;
    const range = sel.getRangeAt(0);
    if (!containerRef.current.contains(range.commonAncestorContainer)) return;

    // Walk all char spans inside the selection range and pick those whose
    // data-offset is within the selected range.
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
      // Filter punctuation; keep CJK + PUA.
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
    workspace.setSelection({
      textid,
      seq,
      start,
      end,
      chars: selChars,
    });
    workspace.setRightTab("annotations");
  }, [textid, seq]);

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
      <div className="tv-body" ref={containerRef}>
        {chars.map((ch, i) => {
          if (PUNCT_RE.test(ch)) {
            return (
              <span key={i} className="pu">
                {ch}
              </span>
            );
          }
          if (ch === "\n") return <br key={i} />;
          const anns = annIndex.byOffset.get(i);
          const has = anns && anns.length > 0;
          const flashing =
            flashOffsets != null && i >= flashOffsets.start && i < flashOffsets.end;
          const cls = `${has ? "ch has-ann" : "ch"}${flashing ? " kwic-flash" : ""}`;
          const title = has ? anns!.map(annTooltip).join(" / ") : undefined;
          return (
            <span
              key={i}
              className={cls}
              data-offset={i}
              title={title}
              onMouseEnter={() => workspace.setHover(ch)}
              onClick={() => {
                if (has) {
                  workspace.setSelection({
                    textid,
                    seq,
                    start: i,
                    end: i + 1,
                    chars: [ch],
                  });
                  workspace.setRightTab("annotations");
                }
              }}
            >
              {ch}
            </span>
          );
        })}
      </div>
    </div>
  );
}
