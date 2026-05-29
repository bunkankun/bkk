import { useCallback, useEffect, useRef, useState } from "react";
import { getTranslationAlignment } from "../../api/client";
import type { TranslationAlignedRow, TranslationAlignmentResponse } from "../../api/types";
import { krRefToChar } from "../../lib/pua";
import { isResizing, useWorkspace, workspace } from "../../state/useWorkspace";

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
}

function SourceText({ row, paneId, tabId }: SourceTextProps) {
  const chars = decodeKrRefs(row.source_text);
  return (
    <>
      {chars.map((c, i) => {
        const absOffset = row.source_offset + c.srcOffset;
        const absEndOffset = row.source_offset + c.srcEndOffset;
        if (PUNCT_RE.test(c.ch)) {
          return <span key={i} className="pu">{c.ch}</span>;
        }
        return (
          <span
            key={i}
            className="ch"
            data-offset={absOffset}
            data-end-offset={absEndOffset}
            data-bucket="body"
            data-anchor-id={row.source_marker_id}
            data-anchor-row-offset={row.source_offset}
            onMouseEnter={() => workspace.setHover(paneId, tabId, c.ch)}
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
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const selectedSegment = useWorkspace((s) => s.selectedSegment);

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
      if (offsets.length === 0) {
        anchorMarkerId = sp.dataset.anchorId ?? null;
        anchorRowOffset = Number(sp.dataset.anchorRowOffset ?? "0");
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

  if (!translationId) {
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
      </div>
      <div className="trans-grid" ref={containerRef}>
        {alignment.rows.map((row) => {
          const isActive =
            selectedSegment?.textid === textid &&
            selectedSegment.seq === seq &&
            selectedSegment.corresp === row.corresp;
          return (
          <div
            className={`trans-row${row.continued ? " continued" : ""}${isActive ? " active" : ""}`}
            key={`${row.source_marker_id}:${row.source_offset}`}
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
                <SourceText row={row} paneId={paneId} tabId={tabId} />
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
