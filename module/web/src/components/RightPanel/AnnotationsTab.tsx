import { useEffect, useState } from "react";
import { getAnnotations } from "../../api/client";
import type { Annotation } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";

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
          “{a.translation.text}”
          {a.translation.src ? ` — ${a.translation.src}` : ""}
        </div>
      )}
    </div>
  );
}

export function AnnotationsTab() {
  const textid = useWorkspace((s) => s.activeTextid);
  const seq = useWorkspace((s) => s.activeSeq);
  const sel = useWorkspace((s) => s.selection);
  const [anns, setAnns] = useState<Annotation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includePin, setIncludePin] = useState(false);

  useEffect(() => {
    if (textid == null || seq == null) {
      setAnns(null);
      return;
    }
    let cancelled = false;
    setAnns(null);
    setError(null);
    getAnnotations(textid, seq)
      .then((a) => {
        if (!cancelled) setAnns(a);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [textid, seq]);

  if (textid == null || seq == null) {
    return <div className="rc empty">Open a juan to see annotations.</div>;
  }
  if (error) return <div className="rc empty">Failed to load: {error}</div>;
  if (!anns) return <div className="rc empty">Loading annotations…</div>;

  // Filter by selection if present.
  let visible = anns;
  if (sel && sel.textid === textid && sel.seq === seq && sel.bucket === "body") {
    visible = anns.filter((a) => a.offset >= sel.start && a.offset < sel.end);
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
      {visible.length === 0 ? (
        <div className="empty">
          {anns.length === 0
            ? "No annotations for this juan."
            : "No annotations in selection."}
        </div>
      ) : (
        visible.map((a, i) => <AnnCard key={a.id ?? `${a.offset}-${i}`} a={a} />)
      )}
    </div>
  );
}
