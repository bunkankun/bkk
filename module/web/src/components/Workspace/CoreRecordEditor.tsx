import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  getCoreList,
  getCoreRecord,
  openCoreRecordPr,
  patchCoreRecord,
} from "../../api/client";
import type {
  CoreEditExtraFile,
  CoreEditResponse,
  CoreMatch,
  CoreRecordResponse,
} from "../../api/types";

// Collection short-name used by the API routes.
type Collection =
  | "concepts"
  | "words"
  | "senses"
  | "syntactic-functions"
  | "semantic-features"
  | "graphs"
  | "bibliography"
  | "super-entries";

// Phase 6 edit UI: per-field controls per record type, with sense add /
// reorder / unlink for words. The pre-Phase-1 raw-YAML textarea is gone;
// fields the editor does not yet cover are still readable via the "Raw
// YAML data" disclosure above the form.
//
// Save model: the first save on a draft creates a fork branch; the
// response's branch + parent_sha are reused on subsequent saves so each
// click stacks one commit. "Open PR" is a follow-up action.
//
// Sense removal is intentionally unlink-only for v1: we drop the UUID
// from sense_uuids but leave the sense file in place (orphan). Deleting
// the file would require its blob sha on the fork branch, which we don't
// have until we read the file back; that round-trip is a follow-up.

type Draft = Record<string, unknown>;

interface Props {
  record: CoreRecordResponse;
  onClose: () => void;
  onSaved: (data: Draft) => void;
}

function newUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback (RFC4122-ish v4) for older runtimes.
  const bytes = new Uint8Array(16);
  for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function shardPath(collection: string, uuid: string): string {
  return `${collection}/${uuid[0]}/${uuid}.yml`;
}

function asString(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  return String(v);
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

function setField(draft: Draft, key: string, value: unknown): Draft {
  if (value === "" || value == null) {
    const { [key]: _, ...rest } = draft;
    return rest;
  }
  return { ...draft, [key]: value };
}

function FormRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: 12, marginBottom: 10 }}>
      <label style={{
        fontSize: 11, color: "var(--t3)", textTransform: "uppercase",
        letterSpacing: 0.4, paddingTop: 4,
      }}>
        {label}
      </label>
      <div>{children}</div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  fontSize: 13,
  padding: "4px 6px",
  background: "var(--bg-1)",
  color: "var(--t1)",
  border: "1px solid var(--bd)",
  borderRadius: 3,
};

const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  fontFamily: "inherit",
  minHeight: 72,
  resize: "vertical",
};

function StringInput({
  value, onChange, placeholder,
}: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.currentTarget.value)}
      style={inputStyle}
    />
  );
}

function ProseInput({
  value, onChange,
}: { value: string; onChange: (v: string) => void }) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.currentTarget.value)}
      style={textareaStyle}
    />
  );
}

function StringListInput({
  value, onChange, placeholder,
}: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const joined = value.join(", ");
  return (
    <input
      type="text"
      value={joined}
      placeholder={placeholder ?? "comma-separated"}
      onChange={(e) => {
        const parts = e.currentTarget.value.split(",").map((s) => s.trim()).filter(Boolean);
        onChange(parts);
      }}
      style={inputStyle}
    />
  );
}

// ---------- relation pickers ----------------------------------------------

type LabelFor = (uuid: string) => string | null;
type Resolved = (uuid: string, label: string) => void;

export interface LabelStore {
  labelFor: LabelFor;
  resolved: Resolved;
}

export function useLabelStore(initial: Map<string, string>): LabelStore {
  const [labels, setLabels] = useState<Record<string, string>>(() =>
    Object.fromEntries(initial),
  );
  const labelFor = useCallback(
    (uuid: string) => labels[uuid] ?? null,
    [labels],
  );
  const resolved = useCallback((uuid: string, label: string) => {
    setLabels((m) => (m[uuid] === label ? m : { ...m, [uuid]: label }));
  }, []);
  return { labelFor, resolved };
}

const chipStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  padding: "2px 4px 2px 6px",
  fontSize: 12,
  background: "var(--bg-1)",
  border: "1px solid var(--bd)",
  borderRadius: 3,
  maxWidth: "100%",
};

function RelationChip({
  uuid,
  collection,
  store,
  onRemove,
}: {
  uuid: string;
  collection: Collection;
  store: LabelStore;
  onRemove?: () => void;
}) {
  const label = store.labelFor(uuid);
  useEffect(() => {
    if (label != null || !uuid) return;
    let cancelled = false;
    getCoreRecord(collection, uuid)
      .then((r) => {
        if (!cancelled) store.resolved(uuid, r.display_label || uuid.slice(0, 8));
      })
      .catch(() => {
        if (!cancelled) store.resolved(uuid, uuid.slice(0, 8));
      });
    return () => {
      cancelled = true;
    };
  }, [uuid, collection, label, store]);
  return (
    <span style={chipStyle} title={uuid}>
      <span
        style={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: 260,
        }}
      >
        {label ?? uuid.slice(0, 8)}
      </span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          style={{
            fontSize: 11,
            lineHeight: 1,
            padding: "0 4px",
            background: "transparent",
            color: "var(--t2)",
            border: "none",
            cursor: "pointer",
          }}
          title="Remove"
        >
          ×
        </button>
      )}
    </span>
  );
}

function RelationSearch({
  collection,
  excludeUuids,
  store,
  onPick,
  placeholder,
}: {
  collection: Collection;
  excludeUuids: Set<string>;
  store: LabelStore;
  onPick: (uuid: string) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [matches, setMatches] = useState<CoreMatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setMatches([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const t = window.setTimeout(() => {
      getCoreList(collection, { q: term, limit: 10 })
        .then((r) => {
          if (cancelled) return;
          setMatches(r.matches.filter((m) => !excludeUuids.has(m.uuid)));
        })
        .catch(() => {
          if (!cancelled) setMatches([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [q, collection, excludeUuids]);

  return (
    <div style={{ position: "relative" }}>
      <input
        type="text"
        value={q}
        placeholder={placeholder ?? `Search ${collection}…`}
        onChange={(e) => setQ(e.currentTarget.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          blurTimer.current = window.setTimeout(() => setOpen(false), 150);
        }}
        style={inputStyle}
      />
      {open && q.trim() && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            zIndex: 10,
            marginTop: 2,
            maxHeight: 220,
            overflowY: "auto",
            background: "var(--bg-pan)",
            border: "1px solid var(--bdr)",
            borderRadius: 3,
            boxShadow: "0 4px 12px rgba(0,0,0,0.35)",
          }}
        >
          {loading && (
            <div style={{ padding: 6, fontSize: 12, color: "var(--t3)" }}>Searching…</div>
          )}
          {!loading && matches.length === 0 && (
            <div style={{ padding: 6, fontSize: 12, color: "var(--t3)" }}>No matches</div>
          )}
          {matches.map((m) => (
            <button
              type="button"
              key={m.uuid}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                store.resolved(m.uuid, m.display_label);
                onPick(m.uuid);
                setQ("");
                setOpen(false);
                if (blurTimer.current) window.clearTimeout(blurTimer.current);
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--hov)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
              }}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "4px 6px",
                fontSize: 12,
                background: "transparent",
                color: "var(--t1)",
                border: "none",
                cursor: "pointer",
              }}
              title={m.uuid}
            >
              <span style={{ fontWeight: 500 }}>{m.display_label}</span>
              {m.alt_labels.length > 0 && (
                <span style={{ color: "var(--t3)", marginLeft: 6 }}>
                  {m.alt_labels.slice(0, 3).join(", ")}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RelationPicker({
  value,
  collection,
  store,
  onChange,
  placeholder,
}: {
  value: string | null;
  collection: Collection;
  store: LabelStore;
  onChange: (v: string | null) => void;
  placeholder?: string;
}) {
  if (value) {
    return (
      <RelationChip
        uuid={value}
        collection={collection}
        store={store}
        onRemove={() => onChange(null)}
      />
    );
  }
  return (
    <RelationSearch
      collection={collection}
      excludeUuids={new Set()}
      store={store}
      onPick={(u) => onChange(u)}
      placeholder={placeholder}
    />
  );
}

function RelationListPicker({
  value,
  collection,
  store,
  onChange,
  placeholder,
}: {
  value: string[];
  collection: Collection;
  store: LabelStore;
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  return (
    <div>
      {value.length > 0 && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: 4,
            marginBottom: 4,
          }}
        >
          {value.map((u, i) => (
            <RelationChip
              key={u}
              uuid={u}
              collection={collection}
              store={store}
              onRemove={() =>
                onChange(value.filter((_, j) => j !== i))
              }
            />
          ))}
        </div>
      )}
      <RelationSearch
        collection={collection}
        excludeUuids={new Set(value)}
        store={store}
        onPick={(u) => onChange([...value, u])}
        placeholder={placeholder}
      />
    </div>
  );
}

// ---------- structured row editors ----------------------------------------

interface BibRef {
  bibliography_uuid: string | null;
  scope: string;
  scope_unit: string;
  note: string;
}

function asBibRefList(v: unknown): BibRef[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const o = item as Record<string, unknown>;
    return [
      {
        bibliography_uuid:
          typeof o.bibliography_uuid === "string" ? o.bibliography_uuid : null,
        scope: asString(o.scope),
        scope_unit: asString(o.scope_unit),
        note: asString(o.note),
      },
    ];
  });
}

function bibRefsToYaml(rows: BibRef[]): Record<string, unknown>[] {
  return rows.map((r) => {
    const out: Record<string, unknown> = {};
    if (r.bibliography_uuid) out.bibliography_uuid = r.bibliography_uuid;
    if (r.scope) out.scope = r.scope;
    if (r.scope_unit) out.scope_unit = r.scope_unit;
    if (r.note) out.note = r.note;
    return out;
  });
}

const smallBtn: React.CSSProperties = {
  fontSize: 11,
  padding: "1px 6px",
  background: "var(--bg-1)",
  color: "var(--t1)",
  border: "1px solid var(--bd)",
  borderRadius: 3,
  cursor: "pointer",
};

const rowBox: React.CSSProperties = {
  border: "1px solid var(--bd)",
  borderRadius: 3,
  padding: 6,
  marginBottom: 6,
};

function BibliographyRefsEditor({
  value,
  store,
  onChange,
}: {
  value: BibRef[];
  store: LabelStore;
  onChange: (v: BibRef[]) => void;
}) {
  const update = (i: number, patch: Partial<BibRef>) =>
    onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => onChange(value.filter((_, j) => j !== i));
  const add = () =>
    onChange([...value, { bibliography_uuid: null, scope: "", scope_unit: "", note: "" }]);
  return (
    <div>
      {value.map((row, i) => (
        <div key={i} style={rowBox}>
          <div style={{ display: "flex", gap: 6, marginBottom: 4 }}>
            <div style={{ flex: 1 }}>
              <RelationPicker
                value={row.bibliography_uuid}
                collection="bibliography"
                store={store}
                onChange={(u) => update(i, { bibliography_uuid: u })}
                placeholder="Search bibliography…"
              />
            </div>
            <button type="button" onClick={() => remove(i)} style={smallBtn} title="Remove">
              ×
            </button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 4 }}>
            <StringInput
              value={row.scope}
              onChange={(v) => update(i, { scope: v })}
              placeholder="scope (e.g. page number)"
            />
            <StringInput
              value={row.scope_unit}
              onChange={(v) => update(i, { scope_unit: v })}
              placeholder="scope_unit (e.g. page)"
            />
          </div>
          <StringInput
            value={row.note}
            onChange={(v) => update(i, { note: v })}
            placeholder="note (optional)"
          />
        </div>
      ))}
      <button type="button" onClick={add} style={smallBtn}>
        + Add bibliography ref
      </button>
    </div>
  );
}

interface KvRow {
  type: string;
  value: string;
}

function asKvList(v: unknown): KvRow[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const o = item as Record<string, unknown>;
    return [{ type: asString(o.type), value: asString(o.value) }];
  });
}

function kvListToYaml(rows: KvRow[]): Record<string, unknown>[] {
  return rows.map((r) => {
    const out: Record<string, unknown> = {};
    if (r.type) out.type = r.type;
    if (r.value) out.value = r.value;
    return out;
  });
}

function UsagesEditor({
  value,
  onChange,
}: {
  value: KvRow[];
  onChange: (v: KvRow[]) => void;
}) {
  const update = (i: number, patch: Partial<KvRow>) =>
    onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => onChange(value.filter((_, j) => j !== i));
  const add = () => onChange([...value, { type: "", value: "" }]);
  return (
    <div>
      {value.map((row, i) => (
        <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
          <StringInput
            value={row.type}
            onChange={(v) => update(i, { type: v })}
            placeholder="usage type"
          />
          <StringInput
            value={row.value}
            onChange={(v) => update(i, { value: v })}
            placeholder="value"
          />
          <button type="button" onClick={() => remove(i)} style={smallBtn} title="Remove">
            ×
          </button>
        </div>
      ))}
      <button type="button" onClick={add} style={smallBtn}>
        + Add usage
      </button>
    </div>
  );
}

interface PronEntry {
  lang: string;
  value: string;
}

function asPronList(v: unknown): PronEntry[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const o = item as Record<string, unknown>;
    return [{ lang: asString(o.lang), value: asString(o.value) }];
  });
}

function pronListToYaml(rows: PronEntry[]): Record<string, unknown>[] {
  return rows.map((r) => {
    const out: Record<string, unknown> = {};
    if (r.lang) out.lang = r.lang;
    if (r.value) out.value = r.value;
    return out;
  });
}

function PronunciationsEditor({
  value,
  onChange,
}: {
  value: PronEntry[];
  onChange: (v: PronEntry[]) => void;
}) {
  const update = (i: number, patch: Partial<PronEntry>) =>
    onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => onChange(value.filter((_, j) => j !== i));
  const add = () => onChange([...value, { lang: "", value: "" }]);
  return (
    <div>
      {value.map((row, i) => (
        <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
          <StringInput
            value={row.lang}
            onChange={(v) => update(i, { lang: v })}
            placeholder="lang (e.g. zh-Latn-x-pinyin)"
          />
          <StringInput
            value={row.value}
            onChange={(v) => update(i, { value: v })}
            placeholder="value"
          />
          <button type="button" onClick={() => remove(i)} style={smallBtn} title="Remove">
            ×
          </button>
        </div>
      ))}
      <button type="button" onClick={add} style={smallBtn}>
        + Add pronunciation
      </button>
    </div>
  );
}

interface SuperForm {
  orth: string;
  graph_uuids: string[];
  pronunciations: PronEntry[];
}

function asSuperFormList(v: unknown): SuperForm[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const o = item as Record<string, unknown>;
    return [
      {
        orth: asString(o.orth),
        graph_uuids: asStringArray(o.graph_uuids),
        pronunciations: asPronList(o.pronunciations),
      },
    ];
  });
}

function superFormListToYaml(rows: SuperForm[]): Record<string, unknown>[] {
  return rows.map((r) => {
    const out: Record<string, unknown> = {};
    if (r.orth) out.orth = r.orth;
    if (r.graph_uuids.length > 0) out.graph_uuids = r.graph_uuids;
    const prons = pronListToYaml(r.pronunciations);
    if (prons.length > 0) out.pronunciations = prons;
    return out;
  });
}

function SuperEntryFormsEditor({
  value,
  store,
  onChange,
}: {
  value: SuperForm[];
  store: LabelStore;
  onChange: (v: SuperForm[]) => void;
}) {
  const update = (i: number, patch: Partial<SuperForm>) =>
    onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => onChange(value.filter((_, j) => j !== i));
  const add = () =>
    onChange([...value, { orth: "", graph_uuids: [], pronunciations: [] }]);
  return (
    <div>
      {value.map((row, i) => (
        <div key={i} style={rowBox}>
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            <div style={{ flex: 1 }}>
              <StringInput
                value={row.orth}
                onChange={(v) => update(i, { orth: v })}
                placeholder="orth"
              />
            </div>
            <button type="button" onClick={() => remove(i)} style={smallBtn} title="Remove form">
              ×
            </button>
          </div>
          <div style={{ marginBottom: 6 }}>
            <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 2 }}>Graphs</div>
            <RelationListPicker
              value={row.graph_uuids}
              collection="graphs"
              store={store}
              onChange={(v) => update(i, { graph_uuids: v })}
              placeholder="Search graphs…"
            />
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 2 }}>Pronunciations</div>
            <PronunciationsEditor
              value={row.pronunciations}
              onChange={(v) => update(i, { pronunciations: v })}
            />
          </div>
        </div>
      ))}
      <button type="button" onClick={add} style={smallBtn}>
        + Add form
      </button>
    </div>
  );
}

// ---------- per-type forms -------------------------------------------------

interface CriterionEntry {
  type: string;
  text: string;
}

function asCriteriaList(v: unknown): CriterionEntry[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const obj = item as Record<string, unknown>;
    return [{ type: asString(obj.type), text: asString(obj.text) }];
  });
}

function CriteriaEditor({
  value, onChange,
}: { value: CriterionEntry[]; onChange: (v: CriterionEntry[]) => void }) {
  const update = (i: number, patch: Partial<CriterionEntry>) => {
    const next = value.map((c, j) => (j === i ? { ...c, ...patch } : c));
    onChange(next);
  };
  const remove = (i: number) => {
    const next = value.filter((_, j) => j !== i);
    onChange(next);
  };
  const add = () => {
    onChange([...value, { type: "old-chinese-criteria", text: "" }]);
  };
  const removeBtn: React.CSSProperties = {
    fontSize: 11, padding: "1px 6px",
    background: "var(--bg-1)", color: "var(--t1)",
    border: "1px solid var(--bd)", borderRadius: 3, cursor: "pointer",
  };
  return (
    <div>
      {value.map((c, i) => (
        <div
          key={i}
          style={{
            border: "1px solid var(--bd)", borderRadius: 3,
            padding: 6, marginBottom: 6,
          }}
        >
          <div style={{ display: "flex", gap: 6, marginBottom: 4 }}>
            <StringInput
              value={c.type}
              onChange={(v) => update(i, { type: v })}
              placeholder="type (e.g. old-chinese-criteria)"
            />
            <button type="button" onClick={() => remove(i)} style={removeBtn} title="Remove criterion">×</button>
          </div>
          <ProseInput value={c.text} onChange={(v) => update(i, { text: v })} />
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        style={{
          fontSize: 12, padding: "3px 10px",
          background: "var(--bg-1)", color: "var(--t1)",
          border: "1px solid var(--bd)", borderRadius: 3, cursor: "pointer",
        }}
      >
        + Add criterion
      </button>
    </div>
  );
}

function ConceptForm({
  draft, set, store,
}: { draft: Draft; set: (key: string, v: unknown) => void; store: LabelStore }) {
  return (
    <>
      <FormRow label="Concept">
        <StringInput value={asString(draft.concept)} onChange={(v) => set("concept", v)} />
      </FormRow>
      <FormRow label="Alt labels">
        <StringListInput value={asStringArray(draft.alt_labels)} onChange={(v) => set("alt_labels", v)} />
      </FormRow>
      <FormRow label="zh">
        <StringInput value={asString(draft.zh)} onChange={(v) => set("zh", v)} />
      </FormRow>
      <FormRow label="och">
        <StringInput value={asString(draft.och)} onChange={(v) => set("och", v)} />
      </FormRow>
      <FormRow label="Definition">
        <ProseInput value={asString(draft.definition)} onChange={(v) => set("definition", v)} />
      </FormRow>
      <FormRow label="Criteria">
        <CriteriaEditor
          value={asCriteriaList(draft.criteria)}
          onChange={(v) => set("criteria", v.length === 0 ? null : v)}
        />
      </FormRow>
      <FormRow label="Antonyms">
        <RelationListPicker
          value={asStringArray(draft.antonyms)}
          collection="concepts"
          store={store}
          onChange={(v) => set("antonyms", v.length === 0 ? null : v)}
        />
      </FormRow>
      <FormRow label="Hypernyms">
        <RelationListPicker
          value={asStringArray(draft.hypernyms)}
          collection="concepts"
          store={store}
          onChange={(v) => set("hypernyms", v.length === 0 ? null : v)}
        />
      </FormRow>
      <FormRow label="Hyponyms">
        <RelationListPicker
          value={asStringArray(draft.hyponyms)}
          collection="concepts"
          store={store}
          onChange={(v) => set("hyponyms", v.length === 0 ? null : v)}
        />
      </FormRow>
      <FormRow label="See also">
        <RelationListPicker
          value={asStringArray(draft.see_also)}
          collection="concepts"
          store={store}
          onChange={(v) => set("see_also", v.length === 0 ? null : v)}
        />
      </FormRow>
      <FormRow label="Bibliography">
        <BibliographyRefsEditor
          value={asBibRefList(draft.bibliography)}
          store={store}
          onChange={(rows) => {
            const yaml = bibRefsToYaml(rows);
            set("bibliography", yaml.length === 0 ? null : yaml);
          }}
        />
      </FormRow>
    </>
  );
}

export function SenseRowLabel({ uuid, store }: { uuid: string; store: LabelStore }) {
  const [rec, setRec] = useState<CoreRecordResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    getCoreRecord("senses", uuid)
      .then((r) => { if (!cancelled) setRec(r); })
      .catch(() => { if (!cancelled) setRec(null); });
    return () => { cancelled = true; };
  }, [uuid]);
  if (!rec) {
    return <span style={{ color: "var(--t3)" }}>{uuid.slice(0, 8)}…</span>;
  }
  const syn = asStringArray(rec.data.syntactic_function_uuids);
  const sem = asStringArray(rec.data.semantic_feature_uuids);
  const definition = asString(rec.data.definition);
  if (syn.length === 0 && sem.length === 0 && !definition) {
    return <span style={{ color: "var(--t3)" }}>{uuid.slice(0, 8)}…</span>;
  }
  return (
    <span>
      {syn.length > 0 && (
        <strong>
          {syn.map((u, i) => (
            <span key={u}>
              {i > 0 && ", "}
              <InlineResolvedLabel uuid={u} collection="syntactic-functions" store={store} />
            </span>
          ))}
        </strong>
      )}
      {sem.length > 0 && (
        <>
          {syn.length > 0 && " "}
          <em>
            {sem.map((u, i) => (
              <span key={u}>
                {i > 0 && ", "}
                <InlineResolvedLabel uuid={u} collection="semantic-features" store={store} />
              </span>
            ))}
          </em>
        </>
      )}
      {definition && (
        <>
          {(syn.length > 0 || sem.length > 0) && " "}
          {definition}
        </>
      )}
    </span>
  );
}

function SenseUuidsEditor({
  uuids, store, onChange,
}: {
  uuids: string[];
  store: LabelStore;
  onChange: (next: string[]) => void;
}) {
  const move = (i: number, delta: number) => {
    const j = i + delta;
    if (j < 0 || j >= uuids.length) return;
    const next = [...uuids];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  const remove = (i: number) => {
    const next = [...uuids];
    next.splice(i, 1);
    onChange(next);
  };
  const btnStyle: React.CSSProperties = {
    fontSize: 11, padding: "1px 6px", marginLeft: 4,
    background: "var(--bg-1)", color: "var(--t1)",
    border: "1px solid var(--bd)", borderRadius: 3, cursor: "pointer",
  };
  return (
    <ol style={{ margin: 0, paddingLeft: 18 }}>
      {uuids.map((u, i) => (
        <li key={u} style={{ marginBottom: 2 }}>
          <SenseRowLabel uuid={u} store={store} />
          <button type="button" style={btnStyle} disabled={i === 0} onClick={() => move(i, -1)} title="Move up">↑</button>
          <button type="button" style={btnStyle} disabled={i === uuids.length - 1} onClick={() => move(i, 1)} title="Move down">↓</button>
          <button type="button" style={btnStyle} onClick={() => remove(i)} title="Unlink from this word (sense file stays as an orphan)">×</button>
        </li>
      ))}
      {uuids.length === 0 && <div style={{ color: "var(--t3)", fontSize: 12 }}>No senses linked.</div>}
    </ol>
  );
}

function WordForm({
  draft, set, store, pendingSenses, onAddSense, onUpdatePendingDef,
}: {
  draft: Draft;
  set: (key: string, v: unknown) => void;
  store: LabelStore;
  pendingSenses: PendingSense[];
  onAddSense: () => void;
  onUpdatePendingDef: (uuid: string, def: string) => void;
}) {
  const form = (draft.form && typeof draft.form === "object")
    ? draft.form as Record<string, unknown>
    : {};
  const setForm = (key: string, value: unknown) => {
    const next = setField(form, key, value);
    set("form", Object.keys(next).length === 0 ? null : next);
  };
  return (
    <>
      <FormRow label="Super-entry">
        <RelationPicker
          value={typeof draft.super_entry_uuid === "string" ? draft.super_entry_uuid : null}
          collection="super-entries"
          store={store}
          onChange={(v) => set("super_entry_uuid", v)}
          placeholder="Search super-entries…"
        />
      </FormRow>
      <FormRow label="Concept">
        <RelationPicker
          value={typeof draft.concept_uuid === "string" ? draft.concept_uuid : null}
          collection="concepts"
          store={store}
          onChange={(v) => set("concept_uuid", v)}
          placeholder="Search concepts…"
        />
      </FormRow>
      <FormRow label="n">
        <StringInput value={asString(draft.n)} onChange={(v) => set("n", v)} />
      </FormRow>
      <FormRow label="Form orth">
        <StringInput value={asString(form.orth)} onChange={(v) => setForm("orth", v)} />
      </FormRow>
      <FormRow label="Form graphs">
        <RelationListPicker
          value={asStringArray(form.graph_uuids)}
          collection="graphs"
          store={store}
          onChange={(v) => setForm("graph_uuids", v.length === 0 ? null : v)}
          placeholder="Search graphs…"
        />
      </FormRow>
      <FormRow label="Pronunciations">
        <PronunciationsEditor
          value={asPronList(form.pronunciations)}
          onChange={(v) => {
            const yaml = pronListToYaml(v);
            setForm("pronunciations", yaml.length === 0 ? null : yaml);
          }}
        />
      </FormRow>
      <FormRow label="Bibliography">
        <BibliographyRefsEditor
          value={asBibRefList(draft.bibliography)}
          store={store}
          onChange={(rows) => {
            const yaml = bibRefsToYaml(rows);
            set("bibliography", yaml.length === 0 ? null : yaml);
          }}
        />
      </FormRow>
      <FormRow label="Senses">
        <SenseUuidsEditor
          uuids={asStringArray(draft.sense_uuids)}
          store={store}
          onChange={(next) => set("sense_uuids", next.length === 0 ? null : next)}
        />
        {pendingSenses.length > 0 && (
          <div style={{ marginTop: 6, padding: 6, border: "1px dashed var(--bd)", borderRadius: 3 }}>
            <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 4 }}>New senses (saved with this edit)</div>
            {pendingSenses.map((s) => (
              <div key={s.uuid} style={{ marginBottom: 6 }}>
                <div style={{ fontSize: 11, color: "var(--t3)" }}>{s.uuid}</div>
                <ProseInput
                  value={s.definition}
                  onChange={(v) => onUpdatePendingDef(s.uuid, v)}
                />
              </div>
            ))}
          </div>
        )}
        <button
          type="button"
          onClick={onAddSense}
          style={{
            marginTop: 6, fontSize: 12, padding: "3px 10px",
            background: "var(--bg-1)", color: "var(--t1)",
            border: "1px solid var(--bd)", borderRadius: 3, cursor: "pointer",
          }}
        >
          + Add sense
        </button>
      </FormRow>
    </>
  );
}

function InlineResolvedLabel({
  uuid,
  collection,
  store,
}: {
  uuid: string;
  collection: Collection;
  store: LabelStore;
}) {
  const label = store.labelFor(uuid);
  useEffect(() => {
    if (label != null || !uuid) return;
    let cancelled = false;
    getCoreRecord(collection, uuid)
      .then((r) => {
        if (!cancelled) store.resolved(uuid, r.display_label || uuid.slice(0, 8));
      })
      .catch(() => {
        if (!cancelled) store.resolved(uuid, uuid.slice(0, 8));
      });
    return () => {
      cancelled = true;
    };
  }, [uuid, collection, label, store]);
  return <>{label ?? uuid.slice(0, 8)}</>;
}

function SenseHeading({
  draft,
  store,
}: {
  draft: Draft;
  store: LabelStore;
}) {
  const syn = asStringArray(draft.syntactic_function_uuids);
  const sem = asStringArray(draft.semantic_feature_uuids);
  const definition = asString(draft.definition);
  if (syn.length === 0 && sem.length === 0 && !definition) {
    return <span style={{ color: "var(--t3)" }}>(new sense — fill below)</span>;
  }
  return (
    <span>
      {syn.length > 0 && (
        <strong>
          {syn.map((u, i) => (
            <span key={u}>
              {i > 0 && ", "}
              <InlineResolvedLabel uuid={u} collection="syntactic-functions" store={store} />
            </span>
          ))}
        </strong>
      )}
      {sem.length > 0 && (
        <>
          {syn.length > 0 && " "}
          <em>
            {sem.map((u, i) => (
              <span key={u}>
                {i > 0 && ", "}
                <InlineResolvedLabel uuid={u} collection="semantic-features" store={store} />
              </span>
            ))}
          </em>
        </>
      )}
      {definition && (
        <>
          {(syn.length > 0 || sem.length > 0) && " "}
          <span>{definition}</span>
        </>
      )}
    </span>
  );
}

function SenseForm({
  draft, set, store,
}: { draft: Draft; set: (key: string, v: unknown) => void; store: LabelStore }) {
  return (
    <>
      <FormRow label="Sense">
        <SenseHeading draft={draft} store={store} />
      </FormRow>
      <FormRow label="POS">
        <StringInput value={asString(draft.pos)} onChange={(v) => set("pos", v)} />
      </FormRow>
      <FormRow label="Attestations (n)">
        <StringInput value={asString(draft.n)} onChange={(v) => set("n", v)} />
      </FormRow>
      <FormRow label="Definition">
        <ProseInput value={asString(draft.definition)} onChange={(v) => set("definition", v)} />
      </FormRow>
      <FormRow label="Syntactic fns">
        <RelationListPicker
          value={asStringArray(draft.syntactic_function_uuids)}
          collection="syntactic-functions"
          store={store}
          onChange={(v) => set("syntactic_function_uuids", v.length === 0 ? null : v)}
          placeholder="Search syntactic-functions…"
        />
      </FormRow>
      <FormRow label="Semantic feats">
        <RelationListPicker
          value={asStringArray(draft.semantic_feature_uuids)}
          collection="semantic-features"
          store={store}
          onChange={(v) => set("semantic_feature_uuids", v.length === 0 ? null : v)}
          placeholder="Search semantic-features…"
        />
      </FormRow>
      <FormRow label="Usages">
        <UsagesEditor
          value={asKvList(draft.usages)}
          onChange={(rows) => {
            const yaml = kvListToYaml(rows);
            set("usages", yaml.length === 0 ? null : yaml);
          }}
        />
      </FormRow>
    </>
  );
}

function TaxonomyForm({
  draft, set, store, collection,
}: {
  draft: Draft;
  set: (key: string, v: unknown) => void;
  store: LabelStore;
  collection: "syntactic-functions" | "semantic-features";
}) {
  return (
    <>
      <FormRow label="Code">
        <StringInput value={asString(draft.code)} onChange={(v) => set("code", v)} />
      </FormRow>
      <FormRow label="Description">
        <ProseInput value={asString(draft.description)} onChange={(v) => set("description", v)} />
      </FormRow>
      <FormRow label="Notes">
        <ProseInput value={asString(draft.notes)} onChange={(v) => set("notes", v)} />
      </FormRow>
      <FormRow label="Taxonomy parents">
        <RelationListPicker
          value={asStringArray(draft.taxonomy_parents)}
          collection={collection}
          store={store}
          onChange={(v) => set("taxonomy_parents", v.length === 0 ? null : v)}
          placeholder={`Search ${collection}…`}
        />
      </FormRow>
      {collection === "semantic-features" && (
        <FormRow label="Source refs">
          <BibliographyRefsEditor
            value={asBibRefList(draft.source_references)}
            store={store}
            onChange={(rows) => {
              const yaml = bibRefsToYaml(rows);
              set("source_references", yaml.length === 0 ? null : yaml);
            }}
          />
        </FormRow>
      )}
    </>
  );
}

function SuperEntryForm({
  draft, set, store,
}: { draft: Draft; set: (key: string, v: unknown) => void; store: LabelStore }) {
  return (
    <>
      <FormRow label="Orth">
        <StringInput value={asString(draft.orth)} onChange={(v) => set("orth", v)} />
      </FormRow>
      <FormRow label="n">
        <StringInput value={asString(draft.n)} onChange={(v) => set("n", v)} />
      </FormRow>
      <FormRow label="Forms">
        <SuperEntryFormsEditor
          value={asSuperFormList(draft.forms)}
          store={store}
          onChange={(rows) => {
            const yaml = superFormListToYaml(rows);
            set("forms", yaml.length === 0 ? null : yaml);
          }}
        />
      </FormRow>
      <FormRow label="Words">
        <RelationListPicker
          value={asStringArray(draft.word_uuids)}
          collection="words"
          store={store}
          onChange={(v) => set("word_uuids", v.length === 0 ? null : v)}
          placeholder="Search words…"
        />
      </FormRow>
    </>
  );
}

function UnsupportedForm({ type }: { type: string }) {
  return (
    <div style={{ fontSize: 12, color: "var(--t3)", padding: 6 }}>
      Structured editing of <code>{type}</code> records is not yet implemented.
      Use the raw YAML view above to inspect the data.
    </div>
  );
}

// ---------- main component -------------------------------------------------

interface PendingSense {
  uuid: string;
  path: string;
  definition: string;
}

export function CoreRecordEditor({ record, onClose, onSaved }: Props) {
  const [draft, setDraft] = useState<Draft>(() =>
    JSON.parse(JSON.stringify(record.data)) as Draft,
  );
  const [pendingSenses, setPendingSenses] = useState<PendingSense[]>([]);
  const [branch, setBranch] = useState<string | null>(null);
  const [parentSha, setParentSha] = useState<string | null>(null);
  const [savedExtras, setSavedExtras] = useState<Map<string, string>>(new Map());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CoreEditResponse | null>(null);
  const [prError, setPrError] = useState<string | null>(null);
  const [prResult, setPrResult] = useState<{ url: string; number: number; existed: boolean } | null>(null);
  const [openingPr, setOpeningPr] = useState(false);

  const set = (key: string, value: unknown) => {
    setDraft((d) => setField(d, key, value));
  };

  const initialLabels = new Map<string, string>();
  for (const l of record.links) {
    if (l.target_label) initialLabels.set(l.target_uuid, l.target_label);
  }
  const store = useLabelStore(initialLabels);

  const addSense = () => {
    const uuid = newUuid();
    const path = shardPath("senses", uuid);
    setPendingSenses((p) => [...p, { uuid, path, definition: "" }]);
    setDraft((d) => {
      const current = asStringArray(d.sense_uuids);
      return { ...d, sense_uuids: [...current, uuid] };
    });
  };

  const updatePendingDef = (uuid: string, definition: string) => {
    setPendingSenses((p) => p.map((s) => s.uuid === uuid ? { ...s, definition } : s));
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      // Drop pending senses that the user removed via the unlink button.
      const draftSenseSet = new Set(asStringArray(draft.sense_uuids));
      const livePending = pendingSenses.filter((s) => draftSenseSet.has(s.uuid));

      const extra_files: CoreEditExtraFile[] = livePending.map((s) => ({
        path: s.path,
        parent_sha: savedExtras.get(s.path),
        data: {
          uuid: s.uuid,
          type: "sense",
          word_uuid: record.uuid,
          ...(s.definition ? { definition: s.definition } : {}),
        },
      }));

      const response = await patchCoreRecord(record.collection, record.uuid, {
        data: draft,
        branch: branch ?? undefined,
        parent_sha: parentSha ?? undefined,
        extra_files,
      });

      setBranch(response.branch);
      setParentSha(response.parent_sha);
      const nextExtras = new Map(savedExtras);
      for (const ex of response.extras ?? []) {
        if (ex.parent_sha) nextExtras.set(ex.path, ex.parent_sha);
      }
      setSavedExtras(nextExtras);
      setPendingSenses(livePending);
      setResult(response);
      onSaved(draft);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const openPr = async () => {
    if (!branch) return;
    setOpeningPr(true);
    setPrError(null);
    try {
      const r = await openCoreRecordPr(record.collection, record.uuid, { branch });
      setPrResult({ url: r.pr_url, number: r.pr_number, existed: r.already_existed });
    } catch (e) {
      setPrError(String(e));
    } finally {
      setOpeningPr(false);
    }
  };

  const form = renderForm(record.type, draft, set, store, pendingSenses, addSense, updatePendingDef, record.uuid);

  return (
    <div
      style={{
        marginTop: 12, padding: 12, border: "1px solid var(--bd)", borderRadius: 4,
        background: "var(--bg-0)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", marginBottom: 10 }}>
        <strong style={{ fontSize: 13, color: "var(--t1)" }}>Edit record</strong>
        <button
          type="button"
          onClick={onClose}
          style={{
            marginLeft: "auto", fontSize: 11, padding: "2px 8px",
            background: "var(--bg-1)", color: "var(--t1)",
            border: "1px solid var(--bd)", borderRadius: 3, cursor: "pointer",
          }}
        >
          Close
        </button>
      </div>

      {form}

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          style={{
            fontSize: 12, padding: "4px 12px",
            background: "var(--accent, #2a5)", color: "white",
            border: "1px solid var(--accent, #2a5)", borderRadius: 3,
            cursor: saving ? "default" : "pointer", opacity: saving ? 0.7 : 1,
          }}
        >
          {saving ? "Saving…" : branch ? "Save (new commit)" : "Save to fork"}
        </button>

        {branch && (
          <button
            type="button"
            onClick={() => void openPr()}
            disabled={openingPr}
            style={{
              fontSize: 12, padding: "4px 12px",
              background: "var(--bg-1)", color: "var(--t1)",
              border: "1px solid var(--bd)", borderRadius: 3,
              cursor: openingPr ? "default" : "pointer",
            }}
          >
            {openingPr ? "Opening PR…" : "Open PR"}
          </button>
        )}

        {result && (
          <a
            href={result.compare_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: 11, color: "var(--link)" }}
          >
            view diff
          </a>
        )}

        {branch && (
          <span style={{ fontSize: 11, color: "var(--t3)", marginLeft: "auto" }}>
            branch: {branch}
          </span>
        )}
      </div>

      {error && (
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--err, #c44)" }}>
          {error}
        </div>
      )}
      {prError && (
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--err, #c44)" }}>
          {prError}
        </div>
      )}
      {prResult && (
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--t2)" }}>
          {prResult.existed ? "Found existing PR" : "Opened PR"}{" "}
          <a href={prResult.url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--link)" }}>
            #{prResult.number}
          </a>
        </div>
      )}
    </div>
  );
}

function renderForm(
  type: string,
  draft: Draft,
  set: (key: string, v: unknown) => void,
  store: LabelStore,
  pendingSenses: PendingSense[],
  addSense: () => void,
  updatePendingDef: (uuid: string, def: string) => void,
  _wordUuid: string,
): ReactNode {
  switch (type) {
    case "concept":
      return <ConceptForm draft={draft} set={set} store={store} />;
    case "word":
      return (
        <WordForm
          draft={draft} set={set} store={store}
          pendingSenses={pendingSenses} onAddSense={addSense}
          onUpdatePendingDef={updatePendingDef}
        />
      );
    case "sense":
      return <SenseForm draft={draft} set={set} store={store} />;
    case "syntactic-function":
      return <TaxonomyForm draft={draft} set={set} store={store} collection="syntactic-functions" />;
    case "semantic-feature":
      return <TaxonomyForm draft={draft} set={set} store={store} collection="semantic-features" />;
    case "super-entry":
      return <SuperEntryForm draft={draft} set={set} store={store} />;
    default:
      return <UnsupportedForm type={type} />;
  }
}
