import { useEffect, useMemo, useRef, useState } from "react";
import {
  allocateBundleMarkerIds,
  getBundleEdit,
  moveBundleSection,
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
  canonicalOffsetAt,
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
type ValidationProblem = { message: string; markerKey?: string };
type MoveDraft = {
  start: number;
  end: number;
  destination: BucketName;
  candidates: BucketName[];
};

let nextMarkerKey = 1;

const ADDABLE_MARKER_TYPES = ["voice", "voice:problem"];

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

function markerOffset(marker: EditableMarker): number | null {
  const offset = marker.data.offset;
  return typeof offset === "number" && Number.isFinite(offset) ? offset : null;
}

function scrollTextareaToPosition(textarea: HTMLTextAreaElement, position: number) {
  const maxScroll = textarea.scrollHeight - textarea.clientHeight;
  if (maxScroll <= 0) return;
  const ratio = Math.max(0, Math.min(position, textarea.value.length)) /
    Math.max(1, textarea.value.length);
  const target = ratio * textarea.scrollHeight - textarea.clientHeight * 0.35;
  textarea.scrollTop = Math.max(0, Math.min(maxScroll, target));
}

function markerIdHasValidShape(id: string, textid: string): boolean {
  const parts = id.split("_", 3);
  return parts.length === 3 && parts[0] === textid && parts[2].length > 0;
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
  const markerListRef = useRef<HTMLDivElement>(null);
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
  const [showLayoutMarkers, setShowLayoutMarkers] = useState(false);
  const [idFind, setIdFind] = useState("");
  const [idReplace, setIdReplace] = useState("");
  const [offsetTarget, setOffsetTarget] = useState("");
  const [moveDraft, setMoveDraft] = useState<MoveDraft | null>(null);
  const [editEdition, setEditEdition] = useState<string | null>(editTarget?.edition ?? null);

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
    setShowLayoutMarkers(false);
    setIdFind("");
    setIdReplace("");
    setOffsetTarget("");
    setMoveDraft(null);
  };

  const reload = () => {
    setLoad({ status: "loading" });
    getBundleEdit(textid, seq, editEdition)
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

  useEffect(() => {
    setEditEdition(editTarget?.edition ?? null);
  }, [textid, seq, editTarget?.edition]);
  useEffect(reload, [textid, seq, editEdition]);
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
    () => [
      ...new Set([
        ...markers.map((marker) => marker.data.type).filter(Boolean),
        ...ADDABLE_MARKER_TYPES,
      ]),
    ].sort(),
    [markers],
  );
  const visibleMarkers = useMemo(
    () => markers.filter((marker) => category === "*" || marker.data.type === category),
    [markers, category],
  );
  const selected = markers.find((marker) => marker.key === selectedKey) ?? null;
  const textLength = useMemo(() => codepoints(text).length, [text]);
  const availablePunctuationSets = useMemo(
    () => punctuationSets(markers),
    [markers],
  );
  const editorView = useMemo(
    () => renderEditorText(text, markers, punctuationSet, showLayoutMarkers),
    [markers, punctuationSet, showLayoutMarkers, text],
  );
  const unresolvedCount = markers.filter((marker) => marker.unresolved).length;
  const idReplaceCount = useMemo(() => {
    if (!idFind) return 0;
    return markers.filter((marker) =>
      typeof marker.data.id === "string" && marker.data.id.includes(idFind)
    ).length;
  }, [idFind, markers]);
  const validationProblem = useMemo((): ValidationProblem | null => {
    const ids = new Map<string, string>();
    for (const [index, marker] of markers.entries()) {
      if (typeof marker.data.type !== "string" || !marker.data.type) {
        return { message: `Marker ${index + 1} needs a type.`, markerKey: marker.key };
      }
      const offset = marker.data.offset;
      if (!Number.isInteger(offset) || Number(offset) < 0 || Number(offset) > textLength) {
        return { message: `Marker ${index + 1} has an invalid offset.`, markerKey: marker.key };
      }
      if (marker.data.length != null) {
        const length = marker.data.length;
        if (
          !Number.isInteger(length) ||
          Number(length) < 0 ||
          Number(offset) + Number(length) > textLength
        ) return { message: `Marker ${index + 1} has an invalid length.`, markerKey: marker.key };
      }
      const id = marker.data.id;
      if (
        typeof id === "string" &&
        id &&
        marker.data.type !== "tls:div-start" &&
        marker.data.type !== "tls:div-end"
      ) {
        const previousKey = ids.get(id);
        if (previousKey) {
          return { message: `Marker ID ${id} is duplicated.`, markerKey: marker.key };
        }
        ids.set(id, marker.key);
      }
      if (
        typeof id === "string" &&
        id &&
        marker.data.type !== "tls:ann" &&
        marker.data.type !== "voice" &&
        !markerIdHasValidShape(id, textid)
      ) {
        return {
          message: `Marker ${index + 1} has malformed ID ${id}; expected ${textid}_<edition>_<location>.`,
          markerKey: marker.key,
        };
      }
    }
    return null;
  }, [markers, text, textid]);
  const validationError = validationProblem?.message ?? null;

  const reportCursor = () => {
    const textarea = textareaRef.current;
    if (!textarea || !onCursorInfoChange) return;
    onCursorInfoChange(editorPositionAt(editorView, textarea.selectionStart));
  };

  const scrollMarkerButtonIntoView = (key: string) => {
    requestAnimationFrame(() => {
      const list = markerListRef.current;
      const button = list?.querySelector<HTMLButtonElement>(`[data-marker-key="${key}"]`);
      button?.scrollIntoView({ block: "nearest" });
    });
  };

  const focusMarkerInTextarea = (marker: EditableMarker) => {
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      const selection = markerDomSelection(editorView, marker);
      textarea.focus();
      textarea.setSelectionRange(selection.start, selection.end);
      scrollTextareaToPosition(textarea, selection.start);
      reportCursor();
    });
  };

  const chooseClosestMarker = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const candidates = visibleMarkers.length > 0 ? visibleMarkers : markers;
    if (candidates.length === 0) return;
    const offset = canonicalOffsetAt(editorView, textarea.selectionStart);
    const best = closestMarkerToOffset(offset, candidates);
    if (!best) return;
    setSelectedKey(best.key);
    scrollMarkerButtonIntoView(best.key);
  };

  const closestMarkerToOffset = (
    offset: number,
    candidates: EditableMarker[],
  ): EditableMarker | null => {
    let best: EditableMarker | null = null;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (const marker of candidates) {
      const markerAt = markerOffset(marker);
      if (markerAt == null) continue;
      const distance = Math.abs(markerAt - offset);
      if (
        distance < bestDistance ||
        (
          distance === bestDistance &&
          best != null &&
          markerAt <= (markerOffset(best) ?? Number.POSITIVE_INFINITY)
        )
      ) {
        best = marker;
        bestDistance = distance;
      }
    }
    return best;
  };

  const jumpToOffset = () => {
    const parsed = Number(offsetTarget);
    const textLength = codepoints(text).length;
    if (!Number.isInteger(parsed) || parsed < 0 || parsed >= textLength) {
      setError(`Offset must be an integer from 0 to ${Math.max(0, textLength - 1)}.`);
      return;
    }
    const textarea = textareaRef.current;
    if (!textarea) return;
    const start = editorView.charStart[parsed] ?? editorView.text.length;
    const end = editorView.caretBefore[parsed + 1] ?? start;
    textarea.focus();
    textarea.setSelectionRange(start, Math.max(start, end));
    scrollTextareaToPosition(textarea, start);
    reportCursor();
    setError(null);
    const candidates = visibleMarkers.length > 0 ? visibleMarkers : markers;
    const best = closestMarkerToOffset(parsed, candidates);
    if (!best) return;
    setSelectedKey(best.key);
    scrollMarkerButtonIntoView(best.key);
  };

  const focusProblemMarker = (problem: ValidationProblem | null) => {
    if (!problem?.markerKey) return;
    const marker = markers.find((item) => item.key === problem.markerKey);
    if (!marker) return;
    setCategory("*");
    setSelectedKey(marker.key);
    scrollMarkerButtonIntoView(marker.key);
    focusMarkerInTextarea(marker);
  };

  const replaceMarkerIds = () => {
    if (!idFind) return;
    let changed = 0;
    const nextMarkers = markers.map((marker) => {
      const id = marker.data.id;
      if (typeof id !== "string" || !id.includes(idFind)) return marker;
      changed += 1;
      return {
        ...marker,
        data: {
          ...marker.data,
          id: id.split(idFind).join(idReplace),
        },
      };
    });
    if (changed === 0) return;
    setMarkers(nextMarkers);
    setDirty(true);
    setSaved(null);
  };

  useEffect(() => {
    if (load.status !== "ready" || !onCursorInfoChange) return;
    const textarea = textareaRef.current;
    const position = textarea?.selectionStart ?? 0;
    onCursorInfoChange(editorPositionAt(editorView, position));
  }, [editorView, load.status, onCursorInfoChange]);

  useEffect(() => {
    if (load.status !== "ready" || editTarget == null) return;
    const targetKey = `${textid}:${seq}:${editEdition ?? ""}:${editTarget.bucket}:${editTarget.markerId}`;
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
    scrollMarkerButtonIntoView(marker.key);
    focusMarkerInTextarea(marker);
  }, [
    bucket,
    editTarget,
    editEdition,
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
      }, editEdition);
      return response.ids;
    } finally {
      setAllocatingIds((count) => Math.max(0, count - 1));
    }
  };

  const changeBucket = (nextBucket: BucketName) => {
    if (dirty && !window.confirm("Discard unsaved edits in this bucket?")) return;
    installBucket(document, nextBucket);
  };

  const changeEdition = (nextEdition: string | null) => {
    if (nextEdition === editEdition) return;
    if (dirty && !window.confirm("Discard unsaved edits and load another edition?")) return;
    setMoveDraft(null);
    setSaved(null);
    setError(null);
    setEditEdition(nextEdition);
  };

  const moveCandidates = (start: number, end: number): BucketName[] => {
    if (end <= start) return [];
    if (bucket === "body") {
      const candidates: BucketName[] = [];
      if (start === 0) candidates.push("front");
      if (end === textLength) candidates.push("back");
      return candidates;
    }
    if (bucket === "front" && end === textLength) return ["body"];
    if (bucket === "back" && start === 0) return ["body"];
    return [];
  };

  const placementLabel = (source: BucketName, destination: BucketName): string => {
    if (source === "body" && destination === "front") return "append to the end of front";
    if (source === "body" && destination === "back") return "prepend to the beginning of back";
    if (source === "front" && destination === "body") return "prepend to the beginning of body";
    if (source === "back" && destination === "body") return "append to the end of body";
    return "move section";
  };

  const openMoveDialog = () => {
    if (dirty) {
      setError("Save or discard edits in this bucket before moving a section.");
      return;
    }
    if (punctuationSet != null || showLayoutMarkers) {
      setError("Turn off punctuation and layout display before moving a section.");
      return;
    }
    const textarea = textareaRef.current;
    if (!textarea) return;
    const selection = canonicalSelectionFromDom(
      editorView,
      textarea.selectionStart,
      textarea.selectionEnd,
    );
    if (selection.length == null || selection.length <= 0) {
      setError("Select the text to move first.");
      return;
    }
    const start = selection.offset;
    const end = selection.offset + selection.length;
    const candidates = moveCandidates(start, end);
    if (candidates.length === 0) {
      setError(
        bucket === "body"
          ? "Body moves must select from the beginning for front, or through the end for back."
          : bucket === "front"
            ? "Front moves must select through the end of front."
            : "Back moves must select from the beginning of back.",
      );
      return;
    }
    setError(null);
    setMoveDraft({ start, end, destination: candidates[0], candidates });
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
    if (showLayoutMarkers) {
      setError("Turn off layout markers before editing text.");
      return;
    }
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
    scrollMarkerButtonIntoView(marker.key);
    focusMarkerInTextarea(marker);
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
      }, editEdition);
      setSaved(result);
      setDirty(false);
      if (result.kind === "pull_request") return;
      setLoad({ status: "loading" });
      try {
        const refreshed = await getBundleEdit(textid, seq, editEdition);
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

  const submitMove = async () => {
    if (!moveDraft) return;
    setSaving(true);
    setError(null);
    try {
      const result = await moveBundleSection(textid, seq, {
        base_commit_sha: document.base_commit_sha,
        source_bucket: bucket,
        destination_bucket: moveDraft.destination,
        start: moveDraft.start,
        end: moveDraft.end,
      }, editEdition);
      setSaved(result);
      setMoveDraft(null);
      if (result.kind === "pull_request") return;
      setLoad({ status: "loading" });
      try {
        const refreshed = await getBundleEdit(textid, seq, editEdition);
        setLoad({ status: "ready", document: refreshed });
        installBucket(refreshed, moveDraft.destination);
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
          <select
            value={editEdition ?? "__master__"}
            title="Edition"
            aria-label="Editable edition"
            onChange={(event) =>
              changeEdition(
                event.target.value === "__master__" ? null : event.target.value,
              )
            }
          >
            {document.editions.map((editionOption) => (
              <option
                key={editionOption.query ?? "__master__"}
                value={editionOption.query ?? "__master__"}
              >
                {editionOption.scope === "master" ? "master" : editionOption.short}
                {editionOption.label ? ` · ${editionOption.label}` : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void addMarker()}
            disabled={allocatingIds > 0}
          >
            Add
          </button>
        </div>
        <form
          className="be-offset-jump"
          onSubmit={(event) => {
            event.preventDefault();
            jumpToOffset();
          }}
        >
          <input
            value={offsetTarget}
            inputMode="numeric"
            aria-label="Scroll to canonical offset"
            placeholder="Offset"
            onChange={(event) => setOffsetTarget(event.target.value)}
          />
          <button type="submit" disabled={!offsetTarget}>Go</button>
        </form>
        <div className="be-id-replace">
          <input
            value={idFind}
            aria-label="Find in marker IDs"
            placeholder="Find ID text"
            onChange={(event) => setIdFind(event.target.value)}
          />
          <input
            value={idReplace}
            aria-label="Replacement marker ID text"
            placeholder="Replace with"
            onChange={(event) => setIdReplace(event.target.value)}
          />
          <button
            type="button"
            disabled={!idFind || idReplaceCount === 0}
            onClick={replaceMarkerIds}
          >
            Replace IDs{ idFind ? ` (${idReplaceCount})` : "" }
          </button>
        </div>
        <div className="be-marker-list" ref={markerListRef}>
          {visibleMarkers.map((marker) => (
            <button
              type="button"
              key={marker.key}
              data-marker-key={marker.key}
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
          {visibleMarkers.length === 0 && (
            <div className="be-list-empty">
              No markers found. Did you commit and push the changes?
            </div>
          )}
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
          <button
            type="button"
            onClick={openMoveDialog}
            disabled={saving || allocatingIds > 0}
          >
            Move
          </button>
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
          <label className="be-punctuation-select">
            <input
              type="checkbox"
              checked={showLayoutMarkers}
              onChange={(event) => {
                setShowLayoutMarkers(event.target.checked);
                setError(null);
              }}
            />
            Layout
          </label>
          <span>{textLength.toLocaleString()} characters</span>
          {unresolvedCount > 0 && <span className="be-unresolved-count">{unresolvedCount} unresolved</span>}
          {validationError && (
            <button
              type="button"
              className="be-validation-jump"
              onClick={() => focusProblemMarker(validationProblem)}
              disabled={!validationProblem?.markerKey}
            >
              {validationError}
            </button>
          )}
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
        {moveDraft && (
          <div className="be-move-panel">
            <span>
              Move {Math.max(0, moveDraft.end - moveDraft.start).toLocaleString()} characters
              from {bucket} offsets {moveDraft.start}-{moveDraft.end}
            </span>
            <select
              value={moveDraft.destination}
              onChange={(event) =>
                setMoveDraft({
                  ...moveDraft,
                  destination: event.target.value as BucketName,
                })
              }
            >
              {moveDraft.candidates.map((candidate) => (
                <option key={candidate} value={candidate}>
                  {candidate}
                </option>
              ))}
            </select>
            <span>{placementLabel(bucket, moveDraft.destination)}</span>
            <button type="button" onClick={() => void submitMove()} disabled={saving}>
              {saving ? "Moving…" : "Apply move"}
            </button>
            <button type="button" onClick={() => setMoveDraft(null)} disabled={saving}>
              Cancel
            </button>
          </div>
        )}
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
          onDoubleClick={chooseClosestMarker}
        />
      </main>
    </div>
  );
}
