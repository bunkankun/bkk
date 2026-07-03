import { useEffect, useMemo, useState } from "react";
import { getBundleEdit, saveBundleEdit } from "../../api/client";
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

type BucketName = "front" | "body" | "back";
type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; document: BundleEditDocument };

let nextMarkerKey = 1;

function editableMarkers(markers: JuanMarker[]): EditableMarker[] {
  return markers.map((data) => ({
    key: `marker-${nextMarkerKey++}`,
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

export function BundleEditor({ textid, seq }: { textid: string; seq: number }) {
  const [load, setLoad] = useState<LoadState>({ status: "loading" });
  const [bucket, setBucket] = useState<BucketName>("body");
  const [text, setText] = useState("");
  const [markers, setMarkers] = useState<EditableMarker[]>([]);
  const [splices, setSplices] = useState<BundleTextSplice[]>([]);
  const [category, setCategory] = useState("*");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<BundleEditSaveResponse | null>(null);
  const [tocDeleteAcknowledged, setTocDeleteAcknowledged] = useState(false);

  const installBucket = (document: BundleEditDocument, nextBucket: BucketName) => {
    const value = document.buckets[nextBucket];
    if (!value) return;
    setBucket(nextBucket);
    setText(value.text);
    const nextMarkers = editableMarkers(value.markers);
    setMarkers(nextMarkers);
    setSelectedKey(nextMarkers[0]?.key ?? null);
    setSplices([]);
    setCategory("*");
    setDirty(false);
    setSaved(null);
    setError(null);
    setTocDeleteAcknowledged(false);
  };

  const reload = () => {
    setLoad({ status: "loading" });
    getBundleEdit(textid, seq)
      .then((document) => {
        setLoad({ status: "ready", document });
        const initialBucket: BucketName =
          document.buckets.body ? "body"
            : document.buckets.front ? "front"
              : "back";
        installBucket(document, initialBucket);
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

  const updateSelected = (data: JuanMarker, resolved = false) => {
    if (!selected) return;
    setMarkers((current) => current.map((marker) =>
      marker.key === selected.key
        ? { ...marker, data, unresolved: resolved ? false : marker.unresolved }
        : marker
    ));
    setDirty(true);
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

  const addMarker = () => {
    const marker: EditableMarker = {
      key: `marker-${nextMarkerKey++}`,
      originalId: null,
      unresolved: false,
      data: { type: category === "*" ? "marker" : category, offset: 0, content: "", id: "" },
    };
    setMarkers((current) => [...current, marker]);
    setSelectedKey(marker.key);
    setDirty(true);
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
    const ordered = [...markers].sort(
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
    setSaving(true);
    setError(null);
    try {
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
          <button type="button" onClick={addMarker}>Add</button>
        </div>
        <div className="be-marker-list">
          {visibleMarkers.map((marker) => (
            <button
              type="button"
              key={marker.key}
              className={`${marker.key === selectedKey ? "on " : ""}${marker.unresolved ? "unresolved" : ""}`}
              onClick={() => setSelectedKey(marker.key)}
            >
              <span>{marker.data.type}</span>
              <small>@{String(marker.data.offset ?? "?")} {String(marker.data.id ?? marker.data.content ?? "")}</small>
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
                  updateSelected(
                    { ...selected.data, [name]: nextValue },
                    name === "offset" || name === "length",
                  )
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
          <span>{codepoints(text).length.toLocaleString()} characters</span>
          {unresolvedCount > 0 && <span className="be-unresolved-count">{unresolvedCount} unresolved</span>}
          {validationError && <span className="be-unresolved-count">{validationError}</span>}
          <span className="be-grow" />
          <button
            type="button"
            onClick={save}
            disabled={!dirty || saving || unresolvedCount > 0 || validationError != null}
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
          value={text}
          aria-label={`${bucket} text`}
          spellCheck={false}
          onChange={(event) => changeText(event.target.value)}
        />
      </main>
    </div>
  );
}
