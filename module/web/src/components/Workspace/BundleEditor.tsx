import { useEffect, useMemo, useRef, useState } from "react";
import {
  allocateBundleMarkerIds,
  getBundleEdit,
  saveBundleEdit,
} from "../../api/client";
import type {
  BundleEditDocument,
  BundleEditSaveResponse,
  BundleTextSplice,
  JuanMarker,
} from "../../api/types";
import {
  codepoints,
  findTextSplice,
  transformMarkers,
  type EditableMarker,
} from "../../lib/editSplices";
import { setBundleEditorDirty } from "../../lib/editorDirty";
import type { BundleEditTarget } from "../../state/useWorkspace";
import {
  canonicalSelectionFromDom,
  editorPositionAt,
  markerDomSelection,
  parsePunctuatedText,
  punctuationInputAllowed,
  punctuationSetLabel,
  punctuationSets,
  reconcilePunctuationMarkers,
  renderEditorText,
  type EditorPosition,
} from "../../lib/editorText";

type BucketName = "front" | "body" | "back";
type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; document: BundleEditDocument };

let nextMarkerKey = 1;

function newMarkerKey(): string {
  return `marker-${nextMarkerKey++}`;
}

function editableMarkers(markers: JuanMarker[]): EditableMarker[] {
  return markers.map((data) => ({
    key: newMarkerKey(),
    data: { ...data },
    originalId: typeof data.id === "string" && data.id ? data.id : null,
    unresolved: false,
  }));
}

function valueKind(value: unknown): "string" | "number" | "boolean" | "json" | "null" {
  if (value === null) return "null";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "object") return "json";
  return "string";
}

function coerceValue(kind: string, text: string): unknown {
  if (kind === "number") return Number(text);
  if (kind === "boolean") return text === "true";
  if (kind === "null") return null;
  if (kind === "json") return JSON.parse(text);
  return text;
}

function PropertyRow({
  name,
  value,
  locked,
  onRename,
  onChange,
  onDelete,
}: {
  name: string;
  value: unknown;
  locked: boolean;
  onRename: (name: string) => void;
  onChange: (value: unknown) => void;
  onDelete: () => void;
}) {
  const kind = valueKind(value);
  const [draft, setDraft] = useState(
    kind === "json" ? JSON.stringify(value) : value == null ? "" : String(value),
  );
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    setDraft(kind === "json" ? JSON.stringify(value) : value == null ? "" : String(value));
    setInvalid(false);
  }, [kind, value]);
  const commit = (nextKind: string, text: string) => {
    try {
      const next = coerceValue(nextKind, text);
      if (nextKind === "number" && !Number.isFinite(next)) throw new Error("bad number");
      onChange(next);
      setInvalid(false);
    } catch {
      setInvalid(true);
    }
  };
  return (
    <div className={`be-prop${invalid ? " invalid" : ""}`}>
      <input
        value={name}
        disabled={locked}
        aria-label="Marker property name"
        onChange={(event) => onRename(event.target.value)}
      />
      <select
        value={kind}
        aria-label={`Type of ${name}`}
        onChange={(event) => {
          const nextKind = event.target.value;
          const seed =
            nextKind === "number" ? "0"
              : nextKind === "boolean" ? "false"
                : nextKind === "json" ? "{}"
                  : "";
          setDraft(seed);
          commit(nextKind, seed);
        }}
      >
        <option value="string">text</option>
        <option value="number">number</option>
        <option value="boolean">boolean</option>
        <option value="json">JSON</option>
        <option value="null">null</option>
      </select>
      {kind === "boolean" ? (
        <select
          value={String(value)}
          onChange={(event) => onChange(event.target.value === "true")}
        >
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
      ) : kind === "null" ? (
        <input value="null" disabled />
      ) : (
        <input
          value={draft}
          aria-label={`Value of ${name}`}
          onChange={(event) => {
            setDraft(event.target.value);
            commit(kind, event.target.value);
          }}
        />
      )}
      <button type="button" disabled={locked} onClick={onDelete} title={`Delete ${name}`}>×</button>
    </div>
  );
}

export function BundleEditor({
  textid,
  seq,
  editTarget,
  onCursorInfoChange,
}: {
  textid: string;
  seq: number;
  editTarget?: BundleEditTarget | null;
  onCursorInfoChange?: (info: EditorPosition) => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const focusedEditTargetRef = useRef<string | null>(null);
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [bucket, setBucket] = useState<BucketName>("body");
  const [text, setText] = useState("");
  const [markers, setMarkers] = useState<EditableMarker[]>([]);
  const [splices, setSplices] = useState<BundleTextSplice[]>([]);
  const [category, setCategory] = useState("*");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [allocatingIds, setAllocatingIds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<BundleEditSaveResponse | null>(null);
  const [tocDeleteAcknowledged, setTocDeleteAcknowledged] = useState(false);
  const [punctuationSet, setPunctuationSet] = useState<string | null>(null);

  const installBucket = (
    document: BundleEditDocument,
    nextBucket: BucketName,
    target: BundleEditTarget | null = editTarget ?? null,
  ) => {
    const value = document.buckets[nextBucket];
    if (!value) return;
    setBucket(nextBucket);
    setText(value.text);
    const nextMarkers = editableMarkers(value.markers);
    setMarkers(nextMarkers);
    const targetMarker = target?.bucket === nextBucket
      ? nextMarkers.find((marker) => marker.data.id === target.markerId) ?? null
      : null;
    setSelectedKey(targetMarker?.key ?? nextMarkers[0]?.key ?? null);
    setSplices([]);
    setCategory("*");
    setDirty(false);
    setSaved(null);
    setError(null);
    setTocDeleteAcknowledged(false);
    setPunctuationSet(null);
  };

  const reload = () => {
    setLoad({ status: "loading" });
    getBundleEdit(textid, seq)
      .then((document) => {
        setLoad({ status: "ready", document });
        const initialBucket: BucketName =
          editTarget?.bucket && document.buckets[editTarget.bucket] ? editTarget.bucket
            : document.buckets.body ? "body"
            : document.buckets.front ? "front"
              : "back";
        installBucket(document, initialBucket, editTarget ?? null);
      })
      .catch((reason) => {
        setLoad({
          status: "error",
          message: reason instanceof Error ? reason.message : String(reason),
        });
      });
  };

  useEffect(reload, [textid, seq]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    setBundleEditorDirty(dirty);
    return () => setBundleEditorDirty(false);
  }, [dirty]);

  const categories = useMemo(
    () => [...new Set(markers.map((marker) => marker.data.type).filter(Boolean))].sort(),
    [markers],
  );
  const visibleMarkers = useMemo(
    () => markers.filter((marker) => category === "*" || marker.data.type === category),
    [markers, category],
  );
  const selected = markers.find((marker) => marker.key === selectedKey) ?? null;
  const availablePunctuationSets = useMemo(
    () => punctuationSets(markers),
    [markers],
  );
  const editorView = useMemo(
    () => renderEditorText(text, markers, punctuationSet),
    [markers, punctuationSet, text],
  );
  const unresolvedCount = markers.filter((marker) => marker.unresolved).length;
  const validationError = useMemo(() => {
    const textLength = codepoints(text).length;
    const ids = new Set<string>();
    for (const [index, marker] of markers.entries()) {
      if (typeof marker.data.type !== "string" || !marker.data.type) {
        return `Marker ${index + 1} needs a type.`;
      }
      const offset = marker.data.offset;
      if (!Number.isInteger(offset) || Number(offset) < 0 || Number(offset) > textLength) {
        return `Marker ${index + 1} has an invalid offset.`;
      }
      if (marker.data.length != null) {
        const length = marker.data.length;
        if (
          !Number.isInteger(length) ||
          Number(length) < 0 ||
          Number(offset) + Number(length) > textLength
        ) return `Marker ${index + 1} has an invalid length.`;
      }
      const id = marker.data.id;
      if (
        typeof id === "string" &&
        id &&
        marker.data.type !== "tls:div-start" &&
        marker.data.type !== "tls:div-end"
      ) {
        if (ids.has(id)) return `Marker ID ${id} is duplicated.`;
        ids.add(id);
      }
    }
    return null;
  }, [markers, text]);

  const reportCursor = () => {
    const textarea = textareaRef.current;
    if (!textarea || !onCursorInfoChange) return;
    onCursorInfoChange(editorPositionAt(editorView, textarea.selectionStart));
  };

  useEffect(() => {
    if (load.status !== "ready" || !onCursorInfoChange) return;
    const textarea = textareaRef.current;
    const position = textarea?.selectionStart ?? 0;
    onCursorInfoChange(editorPositionAt(editorView, position));
  }, [editorView, load.status, onCursorInfoChange]);

  useEffect(() => {
    if (load.status !== "ready" || editTarget == null) return;
    const targetKey = `${textid}:${seq}:${editTarget.bucket}:${editTarget.markerId}`;
    if (bucket !== editTarget.bucket) {
      focusedEditTargetRef.current = null;
      installBucket(load.document, editTarget.bucket, editTarget);
      return;
    }
    const marker = markers.find((item) => item.data.id === editTarget.markerId);
    if (!marker || focusedEditTargetRef.current === targetKey) return;
    focusedEditTargetRef.current = targetKey;
    setCategory("*");
    setSelectedKey(marker.key);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      const selection = markerDomSelection(editorView, marker);
      textarea.focus();
      textarea.setSelectionRange(selection.start, selection.end);
      reportCursor();
    });
  }, [
    bucket,
    editTarget,
    editorView,
    load,
    markers,
    seq,
    textid,
  ]);

  if (load.status === "loading") return <div className="be-empty">Loading editable bundle from GitHub…</div>;
  if (load.status === "error") {
    return (
      <div className="be-empty">
        <div>Editing unavailable: {load.message}</div>
        <button type="button" onClick={reload}>Retry</button>
      </div>
    );
  }
  const document = load.document;

  const requestMarkerIds = async (
    markerTypes: string[],
    occupiedMarkers: EditableMarker[] = markers,
  ): Promise<string[]> => {
    if (markerTypes.length === 0) return [];
    setAllocatingIds((count) => count + 1);
    try {
      const response = await allocateBundleMarkerIds(textid, seq, {
        base_commit_sha: document.base_commit_sha,
        bucket,
        marker_types: markerTypes,
        occupied_ids: occupiedMarkers.flatMap((marker) => {
          const id = marker.data.id;
          return typeof id === "string" && id ? [id] : [];
        }),
      });
      return response.ids;
    } finally {
      setAllocatingIds((count) => Math.max(0, count - 1));
    }
  };

  const changeBucket = (nextBucket: BucketName) => {
    if (dirty && !window.confirm("Discard unsaved edits in this bucket?")) return;
    installBucket(document, nextBucket);
  };

  const changeText = (nextText: string) => {
    const splice = findTextSplice(text, nextText);
    if (!splice) return;
    setText(nextText);
    setSplices((current) => [...current, splice]);
    setMarkers((current) => transformMarkers(current, splice));
    setDirty(true);
    setSaved(null);
  };

  const changeEditorText = (nextText: string) => {
    if (punctuationSet == null) {
      changeText(nextText);
      return;
    }
    if (!punctuationInputAllowed(editorView.text, nextText)) {
      setError("Only punctuation can be inserted while punctuation is loaded.");
      return;
    }
    const parsed = parsePunctuatedText(text, nextText);
    if (!parsed.ok) {
      setError(parsed.message);
      return;
    }
    setMarkers((current) =>
      reconcilePunctuationMarkers(
        current,
        punctuationSet,
        parsed.punctuation,
        newMarkerKey,
      )
    );
    setDirty(true);
    setSaved(null);
    setError(null);
  };

  const updateSelected = (data: JuanMarker, resolved = false) => {
    if (!selected) return;
    setMarkers((current) => current.map((marker) =>
      marker.key === selected.key
        ? { ...marker, data, unresolved: resolved ? false : marker.unresolved }
        : marker
    ));
    setDirty(true);
    setSaved(null);
  };

  const changeSelectedProperty = (name: string, value: unknown) => {
    if (!selected) return;
    const manuallyEditedGeneratedId =
      name === "id" &&
      selected.generatedId === true &&
      value !== selected.data.id;
    const generatedTypeChange =
      name === "type" &&
      selected.generatedId === true &&
      typeof value === "string" &&
      value.length > 0 &&
      value !== selected.data.type;
    const data = {
      ...selected.data,
      [name]: value,
      ...(generatedTypeChange ? { id: "" } : {}),
    };
    if (manuallyEditedGeneratedId) {
      setMarkers((current) => current.map((marker) =>
        marker.key === selected.key
          ? { ...marker, data, generatedId: false }
          : marker
      ));
      setDirty(true);
      setSaved(null);
      return;
    }
    updateSelected(data, name === "offset" || name === "length");
    if (!generatedTypeChange) return;
    const key = selected.key;
    const type = value as string;
    const occupied = markers.filter((marker) => marker.key !== key);
    void requestMarkerIds([type], occupied)
      .then(([id]) => {
        if (!id) return;
        setMarkers((current) => current.map((marker) =>
          marker.key === key &&
          marker.generatedId === true &&
          marker.data.type === type
            ? { ...marker, data: { ...marker.data, id } }
            : marker
        ));
      })
      .catch((reason) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  };

  const deleteSelected = () => {
    if (!selected) return;
    const markerId = typeof selected.data.id === "string" ? selected.data.id : "";
    if (
      markerId &&
      document.toc_marker_ids.includes(markerId) &&
      !window.confirm("This marker is referenced by the table of contents. Delete it and its TOC entries?")
    ) return;
    if (markerId && document.toc_marker_ids.includes(markerId)) setTocDeleteAcknowledged(true);
    setMarkers((current) => current.filter((marker) => marker.key !== selected.key));
    setSelectedKey(null);
    setDirty(true);
  };

  const selectMarker = (marker: EditableMarker) => {
    setSelectedKey(marker.key);
    const selection = markerDomSelection(editorView, marker);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      textarea.focus();
      textarea.setSelectionRange(selection.start, selection.end);
      reportCursor();
    });
  };

  const addMarker = async () => {
    const textarea = textareaRef.current;
    const selection = textarea
      ? canonicalSelectionFromDom(
          editorView,
          textarea.selectionStart,
          textarea.selectionEnd,
        )
      : { offset: 0, length: null };
    const type = category === "*" ? "marker" : category;
    setError(null);
    try {
      const [id] = await requestMarkerIds([type]);
      if (!id) throw new Error("Marker ID allocation returned no ID.");
      const data: JuanMarker = {
        type,
        offset: selection.offset,
        content: "",
        id,
      };
      if (selection.length != null && selection.length > 1) {
        data.length = selection.length;
      }
      const marker: EditableMarker = {
        key: newMarkerKey(),
        originalId: null,
        unresolved: false,
        generatedId: true,
        data,
      };
      setMarkers((current) => [...current, marker]);
      setSelectedKey(marker.key);
      setDirty(true);
      setSaved(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const addProperty = () => {
    if (!selected) return;
    let index = 1;
    let name = "property";
    while (Object.hasOwn(selected.data, name)) name = `property${++index}`;
    updateSelected({ ...selected.data, [name]: "" });
  };

  const save = async () => {
    if (unresolvedCount) {
      setError(`${unresolvedCount} marker${unresolvedCount === 1 ? "" : "s"} still need offset resolution.`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      let workingMarkers = markers;
      const needingIds = workingMarkers.filter((marker) =>
        marker.generatedId === true &&
        (typeof marker.data.id !== "string" || !marker.data.id)
      );
      if (needingIds.length > 0) {
        const allocated = await requestMarkerIds(
          needingIds.map((marker) => marker.data.type),
        );
        const byKey = new Map(
          needingIds.map((marker, index) => [marker.key, allocated[index]]),
        );
        workingMarkers = workingMarkers.map((marker) => {
          const id = byKey.get(marker.key);
          return id ? { ...marker, data: { ...marker.data, id } } : marker;
        });
        setMarkers(workingMarkers);
      }
      const ordered = [...workingMarkers].sort(
        (left, right) => Number(left.data.offset ?? 0) - Number(right.data.offset ?? 0),
      );
      const renamed: Record<string, string> = {};
      for (const marker of ordered) {
        const currentId = typeof marker.data.id === "string" ? marker.data.id : "";
        if (marker.originalId && currentId && marker.originalId !== currentId) {
          renamed[marker.originalId] = currentId;
        }
      }
      const tocRenames = Object.keys(renamed).filter((id) =>
        document.toc_marker_ids.includes(id)
      );
      if (
        tocRenames.length > 0 &&
        !window.confirm(
          `${tocRenames.length} renamed marker${tocRenames.length === 1 ? " is" : "s are"} referenced by the table of contents. Update those TOC references?`,
        )
      ) return;
      const result = await saveBundleEdit(textid, seq, {
        base_commit_sha: document.base_commit_sha,
        bucket,
        text,
        markers: ordered.map((marker) => marker.data),
        text_splices: splices,
        renamed_marker_ids: renamed,
        acknowledge_toc_deletions: tocDeleteAcknowledged,
        unresolved_marker_indexes: [],
      });
      setSaved(result);
      setDirty(false);
      if (result.kind === "pull_request") return;
      setLoad({ status: "loading" });
      try {
        const refreshed = await getBundleEdit(textid, seq);
        setLoad({ status: "ready", document: refreshed });
        installBucket(refreshed, bucket);
        setSaved(result);
      } catch (reloadReason) {
        setLoad({ status: "ready", document });
        setError(
          `Saved, but could not reload the new commit: ${
            reloadReason instanceof Error ? reloadReason.message : String(reloadReason)
          }`,
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bundle-editor">
      <aside className="be-markers">
        <div className="be-toolbar">
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="*">All marker types</option>
            {categories.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          <button
            type="button"
            onClick={() => void addMarker()}
            disabled={allocatingIds > 0}
          >
            Add
          </button>
        </div>
        <div className="be-marker-list">
          {visibleMarkers.map((marker) => (
            <button
              type="button"
              key={marker.key}
              className={`${marker.key === selectedKey ? "on " : ""}${marker.unresolved ? "unresolved" : ""}`}
              onClick={() => selectMarker(marker)}
            >
              <span>{marker.data.type}</span>
              <small>
                @{String(marker.data.offset ?? "?")}
                {marker.data.id ? ` · ${String(marker.data.id)}` : ""}
              </small>
              {typeof marker.data.content === "string" && marker.data.content && (
                <small className="be-marker-content">{marker.data.content}</small>
              )}
            </button>
          ))}
          {visibleMarkers.length === 0 && <div className="be-list-empty">No markers</div>}
        </div>
        {selected && (
          <div className="be-marker-form">
            {selected.unresolved && (
              <div className="be-warning">Text changed across this marker. Set its offset/length to resolve it.</div>
            )}
            {Object.entries(selected.data).map(([name, value]) => (
              <PropertyRow
                key={name}
                name={name}
                value={value}
                locked={name === "type" || name === "offset"}
                onRename={(nextName) => {
                  if (!nextName || Object.hasOwn(selected.data, nextName)) return;
                  const data = { ...selected.data };
                  delete data[name];
                  data[nextName] = value;
                  updateSelected(data);
                }}
                onChange={(nextValue) =>
                  changeSelectedProperty(name, nextValue)
                }
                onDelete={() => {
                  const data = { ...selected.data };
                  delete data[name];
                  updateSelected(data);
                }}
              />
            ))}
            <div className="be-form-actions">
              <button type="button" onClick={addProperty}>Add property</button>
              <button type="button" className="danger" onClick={deleteSelected}>Delete marker</button>
            </div>
          </div>
        )}
      </aside>
      <main className="be-text">
        <div className="be-toolbar">
          <select value={bucket} onChange={(event) => changeBucket(event.target.value as BucketName)}>
            {(Object.keys(document.buckets) as BucketName[]).map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <label className="be-punctuation-select">
            Punctuation
            <select
              value={punctuationSet ?? "__off__"}
              onChange={(event) => {
                setPunctuationSet(
                  event.target.value === "__off__" ? null : event.target.value,
                );
                setError(null);
              }}
            >
              <option value="__off__">Off</option>
              {availablePunctuationSets.map((set) => (
                <option key={set || "__default__"} value={set}>
                  {punctuationSetLabel(set)}
                </option>
              ))}
            </select>
          </label>
          <span>{codepoints(text).length.toLocaleString()} characters</span>
          {unresolvedCount > 0 && <span className="be-unresolved-count">{unresolvedCount} unresolved</span>}
          {validationError && <span className="be-unresolved-count">{validationError}</span>}
          <span className="be-grow" />
          <button
            type="button"
            onClick={save}
            disabled={
              !dirty ||
              saving ||
              allocatingIds > 0 ||
              unresolvedCount > 0 ||
              validationError != null
            }
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        {error && <div className="be-error">{error}</div>}
        {saved && (
          <div className="be-success">
            Saved as {saved.kind === "commit" ? "commit" : "pull request"}:{" "}
            <a href={saved.url} target="_blank" rel="noreferrer">
              {saved.kind === "commit" ? saved.commit_sha.slice(0, 7) : `#${saved.pull_request_number ?? ""}`}
            </a>
          </div>
        )}
        <textarea
          ref={textareaRef}
          value={editorView.text}
          aria-label={`${bucket} text`}
          spellCheck={false}
          onChange={(event) => changeEditorText(event.target.value)}
          onSelect={reportCursor}
          onKeyUp={reportCursor}
          onClick={reportCursor}
        />
      </main>
    </div>
  );
}
