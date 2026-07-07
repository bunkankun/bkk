import { useEffect, useRef, useState } from "react";
import {
  archiveDeleteAnnotation,
  getAnnotations,
  getManifest,
  getSegmentTranslations,
  patchContributionCuration,
  putWorkspaceFile,
  subscribeCoreRecordSaved,
} from "../../api/client";
import type { Annotation, SegmentTranslationEntry } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import {
  locationFilePath,
  selectionContentPreview,
  selectionLocationRef,
  serializeSavedLocation,
  subLocationOffset,
  type SavedSubLocation,
} from "../../lib/namedLocations";
import { AnnotationCompose } from "./AnnotationCompose";
import { ContribCompose } from "./ContribCompose";
import { CoreTargetPicker } from "./CoreTargetPicker";
import { useLabelStore, type LabelStore } from "../Workspace/CoreRecordEditor";
import { AnnotationPayload } from "./AnnotationDisplay";

type AnnAction =
  | { kind: "reject"; uri: string }
  | { kind: "unreject"; uri: string }
  | { kind: "archive-delete" };

const AnnCard = ({
  a,
  store,
  selected,
  cardRef,
  action,
  busy,
  onAction,
}: {
  a: Annotation;
  store: LabelStore;
  selected: boolean;
  cardRef?: (el: HTMLDivElement | null) => void;
  action: AnnAction | null;
  busy: boolean;
  onAction: (a: Annotation, act: AnnAction) => void;
}) => {
  const rejected = a.curation_state === "rejected";
  const className = `ann${selected ? " ann-selected" : ""}${rejected ? " ann-rejected" : ""}`;
  const renderAction = () => {
    if (!action) return null;
    if (action.kind === "unreject") {
      return (
        <button
          className="ann-action ann-unreject"
          disabled={busy}
          title="Restore (set state back to proposed)"
          onClick={(ev) => {
            ev.stopPropagation();
            onAction(a, action);
          }}
        >
          ↺
        </button>
      );
    }
    const title =
      action.kind === "reject"
        ? "Reject (soft delete — hides from indexes)"
        : "Remove this row from the archive";
    return (
      <button
        className="ann-action ann-delete"
        disabled={busy}
        title={title}
        onClick={(ev) => {
          ev.stopPropagation();
          onAction(a, action);
        }}
      >
        ×
      </button>
    );
  };
  return (
    <div
      ref={cardRef}
      className={className}
      onClick={() =>
        workspace.jumpToAnnotation({
          offset: a.offset,
          length: a.length,
          bucket: a.bucket,
        })
      }
    >
      <div className="ann-head">
        {a.form?.orth && <span className="ann-orth">{a.form.orth}</span>}
        {a.form?.pron && <span className="ann-pron">{a.form.pron}</span>}
        <span className="ann-offset">@{a.offset}</span>
        {renderAction()}
      </div>
      <AnnotationPayload
        parts={{
          form: undefined,
          sense: a.sense,
          concept: a.concept,
          translation: a.translation,
        }}
        store={store}
      />
    </div>
  );
};

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

function randomLocationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `loc-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function AnnotationsTab() {
  const textid = useWorkspace((s) => s.activeTextid);
  const seq = useWorkspace((s) => s.activeSeq);
  const sel = useWorkspace((s) => s.selection);
  const selectedSegment = useWorkspace((s) => s.selectedSegment);
  const localAnnotations = useWorkspace((s) => s.localAnnotations);
  const selectedAnnId = useWorkspace((s) => s.selectedAnnotationId);
  const authUser = useWorkspace((s) => s.auth.session?.user ?? null);
  const blueskyEnabled = useWorkspace((s) => s.serverInfo?.bluesky_enabled === true);
  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [anns, setAnns] = useState<Annotation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edition, setEdition] = useState<string | null>(null);
  const [includePin, setIncludePin] = useState(false);
  const [segTranslations, setSegTranslations] = useState<SegmentTranslationEntry[] | null>(null);
  const [segError, setSegError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [locTitle, setLocTitle] = useState("");
  const [locTags, setLocTags] = useState("");
  const [locNote, setLocNote] = useState("");
  const [subNote, setSubNote] = useState("");
  const [subDraft, setSubDraft] = useState<SavedSubLocation | null>(null);
  const [subLocations, setSubLocations] = useState<SavedSubLocation[]>([]);
  const [locSaving, setLocSaving] = useState(false);
  const [locSavedPath, setLocSavedPath] = useState<string | null>(null);
  const [locError, setLocError] = useState<string | null>(null);
  const subTextRef = useRef<HTMLDivElement | null>(null);
  const labelStore = useLabelStore(new Map());

  useEffect(() => {
    return subscribeCoreRecordSaved((event) => {
      labelStore.invalidate(event.uuid);
    });
  }, [labelStore]);

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

  useEffect(() => {
    if (selectedAnnId == null) return;
    if (anns == null) return;
    const el = cardRefs.current.get(selectedAnnId);
    if (el == null) return;
    // Lazy-loaded labels in cards above the target can shift layout after the
    // initial scroll, leaving the target off-center. Re-center a few times
    // until things settle.
    let cancelled = false;
    const recenter = (behavior: ScrollBehavior) => {
      if (cancelled) return;
      el.scrollIntoView({ block: "center", behavior });
    };
    recenter("smooth");
    const t1 = window.setTimeout(() => recenter("auto"), 250);
    const t2 = window.setTimeout(() => recenter("auto"), 700);
    return () => {
      cancelled = true;
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [selectedAnnId, anns]);

  useEffect(() => {
    setLocTitle("");
    setLocTags("");
    setLocNote("");
    setSubNote("");
    setSubDraft(null);
    setSubLocations([]);
    setLocSaving(false);
    setLocSavedPath(null);
    setLocError(null);
  }, [sel]);

  if (textid == null || seq == null) {
    return <div className="rc empty">Open a juan to see annotations.</div>;
  }
  if (error) return <div className="rc empty">Failed to load: {error}</div>;
  if (!anns) return <div className="rc empty">Loading annotations…</div>;

  const localKey = `${textid}_${seq}`;
  const locals = localAnnotations[localKey] ?? [];
  const merged = locals.length > 0 ? [...locals, ...anns] : anns;
  const visible = merged;

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

  const onSubTextMouseUp = () => {
    if (!sel || !subTextRef.current) return;
    const winSel = window.getSelection();
    if (!winSel || winSel.isCollapsed) {
      setSubDraft(null);
      return;
    }
    const range = winSel.getRangeAt(0);
    if (!subTextRef.current.contains(range.commonAncestorContainer)) return;
    const indexes: number[] = [];
    subTextRef.current.querySelectorAll<HTMLElement>("[data-sub-index]").forEach((span) => {
      if (!range.intersectsNode(span)) return;
      const index = Number(span.dataset.subIndex);
      if (Number.isNaN(index)) return;
      indexes.push(index);
    });
    if (indexes.length === 0) {
      setSubDraft(null);
      return;
    }
    indexes.sort((a, b) => a - b);
    const start = indexes[0];
    const end = indexes[indexes.length - 1] + 1;
    setSubDraft({
      offset: subLocationOffset(start, end),
      note: subNote.trim() || undefined,
      content: sel.chars.slice(start, end).join(""),
    });
  };

  const addSubLocation = () => {
    if (!subDraft) return;
    const next = {
      ...subDraft,
      note: subNote.trim() || subDraft.note,
    };
    setSubLocations((prev) => [...prev, next]);
    setSubDraft(null);
    setSubNote("");
    window.getSelection()?.removeAllRanges();
  };

  const removeSubLocation = (index: number) => {
    setSubLocations((prev) => prev.filter((_, i) => i !== index));
  };

  const saveNamedLocation = async () => {
    if (!sel || authUser == null || locSaving) return;
    setLocSaving(true);
    setLocError(null);
    setLocSavedPath(null);
    const now = new Date();
    const path = locationFilePath(now);
    const tags = locTags
      .split(",")
      .map((tag) => tag.trim())
      .filter((tag) => tag.length > 0);
    try {
      await putWorkspaceFile({
        path,
        content: serializeSavedLocation({
          id: randomLocationId(),
          location: selectionLocationRef(sel),
          date: now.toISOString(),
          content: selectionContentPreview(sel.chars),
          title: locTitle.trim() || undefined,
          tags,
          note: locNote.trim() || undefined,
          sub: subLocations,
        }),
      });
      setLocSavedPath(path);
    } catch (e) {
      setLocError(e instanceof Error ? e.message : String(e));
    } finally {
      setLocSaving(false);
    }
  };

  const showSegment =
    selectedSegment != null &&
    selectedSegment.textid === textid &&
    selectedSegment.seq === seq;

  const sessionDid = authUser?.bluesky?.did ?? null;
  const canManage = (a: Annotation): boolean => {
    if (a.id == null) return false;
    if (authUser == null) return false;
    if (authUser.is_editor || authUser.is_admin) return true;
    return sessionDid != null && a.did != null && a.did === sessionDid;
  };

  const resolveAction = (a: Annotation): AnnAction | null => {
    if (!canManage(a)) return null;
    if (a.curation_state === "rejected" && a.uri) {
      if (!blueskyEnabled) return null;
      return { kind: "unreject", uri: a.uri };
    }
    if (a.uri) return blueskyEnabled ? { kind: "reject", uri: a.uri } : null;
    return { kind: "archive-delete" };
  };

  const handleAction = async (a: Annotation, act: AnnAction) => {
    if (a.id == null || textid == null || seq == null) return;
    if (act.kind === "reject" || act.kind === "unreject") {
      const nextState = act.kind === "reject" ? "rejected" : "proposed";
      setBusyId(a.id);
      try {
        await patchContributionCuration(act.uri, { state: nextState });
        setAnns((prev) =>
          prev == null
            ? prev
            : prev.map((x) =>
                x.id === a.id
                  ? { ...x, curation_state: nextState === "proposed" ? undefined : nextState }
                  : x,
              ),
        );
      } catch (e) {
        setError(String(e));
      } finally {
        setBusyId(null);
      }
      return;
    }
    // archive-delete (synth/legacy)
    if (!window.confirm("Remove this annotation row from the archive? This cannot be undone.")) {
      return;
    }
    setBusyId(a.id);
    try {
      await archiveDeleteAnnotation(textid, seq, a.id);
      setAnns((prev) => (prev == null ? prev : prev.filter((x) => x.id !== a.id)));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
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
          <div className="loc-save">
            <div className="loc-save-head">
              <span>Save location</span>
              {authUser == null && <span className="loc-save-status">login required</span>}
              {locSavedPath && <span className="loc-save-status">saved {locSavedPath}</span>}
            </div>
            <input
              className="loc-input"
              value={locTitle}
              onChange={(ev) => setLocTitle(ev.currentTarget.value)}
              placeholder="Title"
              disabled={authUser == null || locSaving}
            />
            <input
              className="loc-input"
              value={locTags}
              onChange={(ev) => setLocTags(ev.currentTarget.value)}
              placeholder="Tags"
              disabled={authUser == null || locSaving}
            />
            <textarea
              className="loc-note"
              value={locNote}
              onChange={(ev) => setLocNote(ev.currentTarget.value)}
              placeholder="Note"
              disabled={authUser == null || locSaving}
            />
            <div
              ref={subTextRef}
              className="loc-subtext"
              onMouseUp={onSubTextMouseUp}
              title="Select text here to add a sub-selection"
            >
              {sel.chars.map((ch, index) => (
                <span key={index} data-sub-index={index}>{ch}</span>
              ))}
            </div>
            <div className="loc-subrow">
              <input
                className="loc-input"
                value={subNote}
                onChange={(ev) => {
                  const note = ev.currentTarget.value;
                  setSubNote(note);
                  setSubDraft((draft) =>
                    draft ? { ...draft, note: note.trim() || undefined } : draft,
                  );
                }}
                placeholder="Sub-selection note"
                disabled={authUser == null || locSaving}
              />
              <button
                type="button"
                className="sel-action loc-sub-add"
                onClick={addSubLocation}
                disabled={authUser == null || locSaving || subDraft == null}
              >
                Add sub
              </button>
            </div>
            {subDraft && (
              <div className="loc-draft">
                {subDraft.offset} {subDraft.content}
              </div>
            )}
            {subLocations.length > 0 && (
              <div className="loc-sublist">
                {subLocations.map((item, index) => (
                  <div className="loc-subitem" key={`${item.offset}-${index}`}>
                    <span>{item.offset}</span>
                    <span>{item.content}</span>
                    <span>{item.note ?? ""}</span>
                    <button type="button" onClick={() => removeSubLocation(index)}>×</button>
                  </div>
                ))}
              </div>
            )}
            {locError && <div className="loc-error">{locError}</div>}
            <button
              type="button"
              className="sel-action loc-save-btn"
              onClick={saveNamedLocation}
              disabled={authUser == null || locSaving}
            >
              {locSaving ? "Saving..." : "Save location"}
            </button>
          </div>
          <CoreTargetPicker selection={sel} edition={edition} />
          {blueskyEnabled && edition && (
            <AnnotationCompose selection={sel} edition={edition} />
          )}
          {blueskyEnabled && edition && (
            <ContribCompose selection={sel} edition={edition} />
          )}
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
        <div className="empty">No annotations for this juan.</div>
      ) : (
        visible.map((a, i) => {
          const key = a.id ?? `${a.offset}-${i}`;
          const isSelected = a.id != null && a.id === selectedAnnId;
          const action = resolveAction(a);
          const busy = a.id != null && a.id === busyId;
          return (
            <AnnCard
              key={key}
              a={a}
              store={labelStore}
              selected={isSelected}
              cardRef={
                a.id != null
                  ? (el) => {
                      if (el == null) cardRefs.current.delete(a.id!);
                      else cardRefs.current.set(a.id!, el);
                    }
                  : undefined
              }
              action={action}
              busy={busy}
              onAction={handleAction}
            />
          );
        })
      )}
    </div>
  );
}
