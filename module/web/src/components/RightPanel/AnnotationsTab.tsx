import { useEffect, useState } from "react";
import { getAnnotations, getManifest, getSegmentTranslations } from "../../api/client";
import type { Annotation, SegmentTranslationEntry } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { AnnotationCompose } from "./AnnotationCompose";
import { CoreTargetPicker } from "./CoreTargetPicker";

function AnnCard({ a }: { a: Annotation }) {
  return (
    <div className="ann">
      <div className="ann-head">
        {a.form?.orth && <span className="ann-orth">{a.form.orth}</span>}
        {a.form?.pron && <span className="ann-pron">{a.form.pron}</span>}
        <span className="ann-offset">@{a.offset}</span>
      </div>
      {a.concept && <div className="ann-concept">{a.concept}</div>}
      {a.sense?.def && <div className="ann-def">{a.sense.def}</div>}
      {a.translation?.text && (
        <div className="ann-tr">
          "{a.translation.text}"
          {a.translation.src ? ` — ${a.translation.src}` : ""}
        </div>
      )}
    </div>
  );
}

function SegTransCard({ entry, textid }: { entry: SegmentTranslationEntry; textid: string }) {
  const onLoad = () => {
    workspace.selectTranslation({
      id: entry.bundle_id,
      source_textid: textid,
      language: entry.language ?? null,
      title: entry.title ?? null,
      original_title: null,
      canonical_identifier: null,
      source_canonical_identifier: null,
      responsibility: entry.translator
        ? [{ role: "translator", name: entry.translator }]
        : [],
      date: null,
      license: null,
      juan_count: 0,
      segment_count: 0,
      source_juans: [],
    });
  };
  return (
    <div className="ann seg-trans">
      <div className="ann-head">
        {entry.language && <span className="ann-orth">{entry.language}</span>}
        {entry.translator && <span className="ann-pron">{entry.translator}</span>}
        <button className="seg-trans-load" onClick={onLoad} title="Open this translation">↗</button>
      </div>
      {entry.title && <div className="ann-concept">{entry.title}</div>}
      <div className="ann-tr">"{entry.text}"</div>
    </div>
  );
}

export function AnnotationsTab() {
  const textid = useWorkspace((s) => s.activeTextid);
  const seq = useWorkspace((s) => s.activeSeq);
  const sel = useWorkspace((s) => s.selection);
  const selectedSegment = useWorkspace((s) => s.selectedSegment);
  const localAnnotations = useWorkspace((s) => s.localAnnotations);
  const [anns, setAnns] = useState<Annotation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edition, setEdition] = useState<string | null>(null);
  const [includePin, setIncludePin] = useState(false);
  const [segTranslations, setSegTranslations] = useState<SegmentTranslationEntry[] | null>(null);
  const [segError, setSegError] = useState<string | null>(null);

  useEffect(() => {
    if (textid == null || seq == null) {
      setAnns(null);
      return;
    }
    let cancelled = false;
    setAnns(null);
    setError(null);
    getAnnotations(textid, seq)
      .then((a) => { if (!cancelled) setAnns(a); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [textid, seq]);

  useEffect(() => {
    if (textid == null) {
      setEdition(null);
      return;
    }
    let cancelled = false;
    getManifest(textid)
      .then((m) => {
        if (cancelled) return;
        setEdition(m.metadata?.edition?.short ?? null);
      })
      .catch(() => {
        if (!cancelled) setEdition(null);
      });
    return () => { cancelled = true; };
  }, [textid]);

  useEffect(() => {
    if (
      selectedSegment == null ||
      selectedSegment.textid !== textid ||
      selectedSegment.seq !== seq
    ) {
      setSegTranslations(null);
      setSegError(null);
      return;
    }
    let cancelled = false;
    setSegTranslations(null);
    setSegError(null);
    getSegmentTranslations(
      selectedSegment.textid,
      selectedSegment.seq,
      selectedSegment.corresp,
      selectedSegment.sourceText,
    )
      .then((r) => { if (!cancelled) setSegTranslations(r.entries); })
      .catch((e) => { if (!cancelled) setSegError(String(e)); });
    return () => { cancelled = true; };
  }, [selectedSegment, textid, seq]);

  if (textid == null || seq == null) {
    return <div className="rc empty">Open a juan to see annotations.</div>;
  }
  if (error) return <div className="rc empty">Failed to load: {error}</div>;
  if (!anns) return <div className="rc empty">Loading annotations…</div>;

  const localKey = `${textid}_${seq}`;
  const locals = localAnnotations[localKey] ?? [];
  const merged = locals.length > 0 ? [...locals, ...anns] : anns;

  // Filter by selection if present.
  let visible = merged;
  if (sel && sel.textid === textid && sel.seq === seq && sel.bucket === "body") {
    visible = merged.filter((a) => a.offset >= sel.start && a.offset < sel.end);
  } else if (sel && sel.textid === textid && sel.seq === seq) {
    visible = [];
  }

  const selectionLines = sel
    ? [
        "selection:",
        `  juan: ${sel.seq}`,
        `  bucket: ${sel.bucket}`,
        `  offset: ${sel.start}`,
        `  length: ${Math.max(0, sel.end - sel.start)}`,
      ]
    : null;

  const refString =
    sel && selectionLines
      ? includePin
        ? [
            "- role: base",
            `  textid: ${sel.textid}`,
            "  selection:",
            `    juan: ${sel.seq}`,
            `    bucket: ${sel.bucket}`,
            `    offset: ${sel.start}`,
            `    length: ${Math.max(0, sel.end - sel.start)}`,
          ].join("\n")
        : selectionLines.join("\n")
      : null;

  const onCopyRef = async () => {
    if (!refString) return;
    try {
      await navigator.clipboard.writeText(refString);
    } catch {
      /* clipboard blocked — silently swallow */
    }
  };

  const onSearchSelection = () => {
    if (!sel) return;
    workspace.setSearchQuery(sel.chars.join(""));
    void workspace.runSearch();
  };

  const showSegment =
    selectedSegment != null &&
    selectedSegment.textid === textid &&
    selectedSegment.seq === seq;

  return (
    <div className="rc">
      {sel && sel.textid === textid && sel.seq === seq && (
        <>
          <div className="sel-summary">{sel.chars.join("")}</div>
          <div className="sel-meta">
            <span title={`${sel.bucket} master_offset [${sel.start}, ${sel.end})`}>
              {sel.anchorMarkerId
                ? `${sel.bucket} @ ${sel.anchorMarkerId}${sel.anchorOffset > 0 ? ` + ${sel.anchorOffset}` : ""}`
                : `${sel.bucket} @ offset ${sel.start}`}
            </span>
            <span>{sel.chars.length} char</span>
            <button
              className="sel-clear"
              onClick={() => workspace.setSelection(null)}
            >
              clear
            </button>
          </div>
          <div className="sel-actions">
            <button
              className="sel-action"
              onClick={onCopyRef}
              title={refString ?? undefined}
            >
              Copy ref
            </button>
            <button className="sel-action" onClick={onSearchSelection}>
              Search this
            </button>
          </div>
          <CoreTargetPicker selection={sel} edition={edition} />
          {edition && <AnnotationCompose selection={sel} edition={edition} />}
          <label className="sel-pin-toggle">
            <input
              type="checkbox"
              checked={includePin}
              onChange={(ev) => setIncludePin(ev.currentTarget.checked)}
            />
            Include pin
          </label>
        </>
      )}

      {showSegment && (
        <div className="seg-trans-panel">
          <div className="seg-trans-header">
            <span className="seg-trans-corresp">{selectedSegment!.corresp}</span>
            <span className="seg-trans-text">{selectedSegment!.sourceText}</span>
            <button
              className="sel-clear"
              onClick={() => workspace.setSelectedSegment(null)}
            >
              ×
            </button>
          </div>
          {segError && <div className="empty">{segError}</div>}
          {segTranslations == null && !segError && (
            <div className="empty">Loading translations…</div>
          )}
          {segTranslations != null && segTranslations.length === 0 && (
            <div className="empty">No translations for this segment.</div>
          )}
          {segTranslations?.map((entry, i) => (
            <SegTransCard key={`${entry.bundle_id}-${i}`} entry={entry} textid={textid!} />
          ))}
        </div>
      )}

      {visible.length === 0 ? (
        <div className="empty">
          {merged.length === 0
            ? "No annotations for this juan."
            : "No annotations in selection."}
        </div>
      ) : (
        visible.map((a, i) => <AnnCard key={a.id ?? `${a.offset}-${i}`} a={a} />)
      )}
    </div>
  );
}
