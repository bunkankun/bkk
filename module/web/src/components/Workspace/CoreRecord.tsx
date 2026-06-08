import { useEffect, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  deleteCoreRecord,
  getAnnotationSenseCounts,
  getCoreBacklinks,
  getCoreConceptWords,
  getCoreRecord,
  getCoreSuperEntryByOrth,
} from "../../api/client";
import type {
  CoreBacklinksResponse,
  CoreConceptWord,
  CoreRecordLink,
  CoreRecordResponse,
} from "../../api/types";
import { findTab, useWorkspace, workspace } from "../../state/useWorkspace";
import { SenseUsesPanel } from "../SenseUses";
import { CoreRecordEditor } from "./CoreRecordEditor";

const WIKILINK_SCHEME = "bkk-wikilink:";

function passthroughUrlTransform(href: string): string {
  return href;
}

// Convert `[[X]]` (CJK) wikilinks to markdown links with a sentinel scheme so
// ReactMarkdown's link handler can intercept and dispatch them.
function preprocessWikilinks(text: string): string {
  return text.replace(/\[\[([^\]\n|]+)\]\]/g, (_, inner: string) => {
    const orth = inner.trim();
    if (!orth) return _;
    return `[${orth}](${WIKILINK_SCHEME}${encodeURIComponent(orth)})`;
  });
}

function SenseLink({ uuid, label }: { uuid: string; label: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <a
        href={`#${uuid}`}
        onClick={(e) => {
          e.preventDefault();
          setExpanded((v) => !v);
        }}
        style={{ color: "var(--link)" }}
      >
        {label}
      </a>
      {expanded && <SenseUsesPanel senseUuid={uuid} />}
    </>
  );
}

type State =
  | { status: "loading" }
  | { status: "ok"; record: CoreRecordResponse }
  | { status: "error"; error: string; status_code?: number };

type Lookup = (uuid: string) => CoreRecordLink | undefined;
type Navigate = (collection: string, uuid: string) => void;

function recordLink(
  uuid: string,
  lookup: Lookup,
  navigate: Navigate,
  fallbackCollection?: string,
): ReactNode {
  const link = lookup(uuid);
  const label = link?.target_label ?? uuid;
  const collection = link?.target_collection ?? fallbackCollection ?? null;
  if (!collection) {
    return <span>{label}</span>;
  }
  return (
    <a
      href="#"
      onClick={(e) => {
        e.preventDefault();
        navigate(collection, uuid);
      }}
      style={{ color: "var(--link)" }}
    >
      {label}
    </a>
  );
}

function ProseMarkdown({
  text,
  onWikilink,
  onCoreNav,
}: {
  text: string;
  onWikilink: (orth: string) => void;
  onCoreNav: Navigate;
}) {
  const processed = useMemo(() => preprocessWikilinks(text || ""), [text]);
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={passthroughUrlTransform}
      components={{
        a: ({ href, children, ...rest }) => {
          if (href?.startsWith(WIKILINK_SCHEME)) {
            const orth = decodeURIComponent(href.slice(WIKILINK_SCHEME.length));
            return (
              <a
                href={href}
                onClick={(e) => {
                  e.preventDefault();
                  onWikilink(orth);
                }}
                style={{ color: "var(--link)" }}
                {...rest}
              >
                {children}
              </a>
            );
          }
          // Bare-UUID markdown link, written by the importer for `{{BKKREF:…}}`.
          // We don't know the collection so we let the backend's link table
          // resolve it lazily; for now navigate to whichever collection the
          // referenced uuid lives in by hitting `/core/<any>/<uuid>` would 404
          // for the wrong collection. Surface as a plain visible label.
          if (href && /^[0-9a-fA-F-]{36}$/.test(href)) {
            const uuid = href;
            return (
              <a
                href={`#${uuid}`}
                onClick={(e) => {
                  e.preventDefault();
                  // Best-effort: jump into concepts first; user can navigate
                  // from there. Phase 5 will switch this to the typed link.
                  onCoreNav("concepts", uuid);
                }}
                style={{ color: "var(--link)" }}
              >
                {children}
              </a>
            );
          }
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--link)" }}
              {...rest}
            >
              {children}
            </a>
          );
        },
      }}
    >
      {processed}
    </ReactMarkdown>
  );
}

function FieldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: 12, marginBottom: 6 }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: 0.4 }}>
        {label}
      </div>
      <div style={{ fontSize: 13, color: "var(--t1)" }}>{children}</div>
    </div>
  );
}

function UuidList({
  uuids,
  lookup,
  navigate,
  fallbackCollection,
}: {
  uuids: string[];
  lookup: Lookup;
  navigate: Navigate;
  fallbackCollection?: string;
}) {
  if (!uuids?.length) return null;
  return (
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      {uuids.map((u) => (
        <li key={u}>{recordLink(u, lookup, navigate, fallbackCollection)}</li>
      ))}
    </ul>
  );
}

function asString(v: unknown): string | null {
  if (v == null) return null;
  if (typeof v === "string") return v;
  return String(v);
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string" && x.length > 0);
}

function asRecordArray(v: unknown): Record<string, unknown>[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is Record<string, unknown> => typeof x === "object" && x != null);
}

// ---------- per-type renderers ---------------------------------------------

function ConceptView({
  data,
  lookup,
  navigate,
  onWikilink,
}: {
  data: Record<string, unknown>;
  lookup: Lookup;
  navigate: Navigate;
  onWikilink: (orth: string) => void;
}) {
  const concept = asString(data.concept);
  const altLabels = asStringArray(data.alt_labels);
  const zh = asString(data.zh);
  const och = asString(data.och);
  const definition = asString(data.definition);
  const criteria = asRecordArray(data.criteria);
  const antonyms = asStringArray(data.antonyms);
  const hypernyms = asStringArray(data.hypernyms);
  const hyponyms = asStringArray(data.hyponyms);
  const seeAlso = asStringArray(data.see_also);
  const otherRelations = asRecordArray(data.other_relations);
  const bibliography = asRecordArray(data.bibliography);
  const wordsText = asString(data.words_text);
  return (
    <>
      {concept && <FieldRow label="Concept">{concept}</FieldRow>}
      {altLabels.length > 0 && (
        <FieldRow label="Also">{altLabels.join(", ")}</FieldRow>
      )}
      {zh && <FieldRow label="zh">{zh}</FieldRow>}
      {och && <FieldRow label="och">{och}</FieldRow>}
      {definition && (
        <FieldRow label="Definition">
          <ProseMarkdown text={definition} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
      {criteria.map((section, i) => {
        const type = asString(section.type) ?? "criteria";
        const text = asString(section.text) ?? "";
        return (
          <FieldRow key={`${type}-${i}`} label={type}>
            <ProseMarkdown text={text} onWikilink={onWikilink} onCoreNav={navigate} />
          </FieldRow>
        );
      })}
      {antonyms.length > 0 && (
        <FieldRow label="Antonyms">
          <UuidList uuids={antonyms} lookup={lookup} navigate={navigate} fallbackCollection="concepts" />
        </FieldRow>
      )}
      {hypernyms.length > 0 && (
        <FieldRow label="Hypernyms">
          <UuidList uuids={hypernyms} lookup={lookup} navigate={navigate} fallbackCollection="concepts" />
        </FieldRow>
      )}
      {hyponyms.length > 0 && (
        <FieldRow label="Hyponyms">
          <UuidList uuids={hyponyms} lookup={lookup} navigate={navigate} fallbackCollection="concepts" />
        </FieldRow>
      )}
      {seeAlso.length > 0 && (
        <FieldRow label="See also">
          <UuidList uuids={seeAlso} lookup={lookup} navigate={navigate} fallbackCollection="concepts" />
        </FieldRow>
      )}
      {otherRelations.map((rel, i) => {
        const type = asString(rel.type) ?? `relation-${i}`;
        const uuids = asStringArray(rel.uuids);
        if (uuids.length === 0) return null;
        return (
          <FieldRow key={`${type}-${i}`} label={type}>
            <UuidList uuids={uuids} lookup={lookup} navigate={navigate} fallbackCollection="concepts" />
          </FieldRow>
        );
      })}
      {bibliography.length > 0 && (
        <FieldRow label="Bibliography">
          <BibliographyRefList refs={bibliography} lookup={lookup} navigate={navigate} />
        </FieldRow>
      )}
      {wordsText && (
        <FieldRow label="Words">
          <ProseMarkdown text={wordsText} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
    </>
  );
}

function BibliographyRefList({
  refs,
  lookup,
  navigate,
}: {
  refs: Record<string, unknown>[];
  lookup: Lookup;
  navigate: Navigate;
}) {
  return (
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      {refs.map((ref, i) => {
        const uuid = asString(ref.bibliography_uuid);
        const scope = asString(ref.scope);
        const scopeUnit = asString(ref.scope_unit);
        const notes = asStringArray(ref.notes);
        return (
          <li key={`${uuid ?? "ref"}-${i}`}>
            {uuid ? recordLink(uuid, lookup, navigate, "bibliography") : <em>(missing ref)</em>}
            {scope && (
              <span style={{ color: "var(--t3)" }}>
                {" "}— {scope}
                {scopeUnit && ` (${scopeUnit})`}
              </span>
            )}
            {notes.length > 0 && (
              <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>
                {notes.map((n, j) => (
                  <li key={j} style={{ color: "var(--t2)" }}>{n}</li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function pronLabel(lang: string): { label: string | null; italic: boolean } {
  if (lang === "zh-Latn-x-pinyin") return { label: null, italic: true };
  if (lang === "zh-x-oc") return { label: "OC", italic: false };
  if (lang === "zh-x-mc") return { label: "MC", italic: false };
  return { label: lang, italic: false };
}

function FormInline({
  form,
  superEntryUuid,
  lookup,
  navigate,
}: {
  form: Record<string, unknown>;
  superEntryUuid: string | null;
  lookup: Lookup;
  navigate: Navigate;
}) {
  const orth = asString(form.orth);
  const graphUuids = asStringArray(form.graph_uuids);
  const prons = asRecordArray(form.pronunciations);
  const parts: ReactNode[] = [];
  if (orth) {
    parts.push(
      superEntryUuid ? (
        <a
          key="orth"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("super-entries", superEntryUuid);
          }}
          style={{ color: "var(--link)" }}
        >
          {orth}
        </a>
      ) : (
        <span key="orth">{orth}</span>
      ),
    );
  }
  prons.forEach((p, i) => {
    const lang = asString(p.lang);
    const value = asString(p.value);
    if (!value) return;
    const { label, italic } = pronLabel(lang ?? "");
    parts.push(
      <span key={`pron-${i}`}>
        {label && <span style={{ color: "var(--t3)" }}>{label}: </span>}
        {italic ? <em>{value}</em> : value}
      </span>,
    );
  });
  if (graphUuids.length > 0) {
    parts.push(
      <span key="graphs" style={{ color: "var(--t3)" }}>
        graphs:{" "}
        {graphUuids.map((u, i) => (
          <span key={u}>
            {i > 0 && ", "}
            {recordLink(u, lookup, navigate, "graphs")}
          </span>
        ))}
      </span>,
    );
  }
  return (
    <span>
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 && <span style={{ color: "var(--t3)" }}> · </span>}
          {p}
        </span>
      ))}
    </span>
  );
}

interface SenseCountsState {
  status: "loading" | "ok" | "na";
  counts: Record<string, number>;
  total: number;
}

function useSenseCounts(senseUuids: string[]): SenseCountsState {
  const [state, setState] = useState<SenseCountsState>({
    status: "loading", counts: {}, total: 0,
  });
  const key = senseUuids.join(",");
  useEffect(() => {
    if (senseUuids.length === 0) {
      setState({ status: "ok", counts: {}, total: 0 });
      return;
    }
    let cancelled = false;
    setState({ status: "loading", counts: {}, total: 0 });
    getAnnotationSenseCounts(senseUuids)
      .then((r) => {
        if (cancelled) return;
        const total = senseUuids.reduce((n, u) => n + (r.counts[u] ?? 0), 0);
        setState({ status: "ok", counts: r.counts, total });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "na", counts: {}, total: 0 });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}

function AttributionsBadge({
  senseUuid,
  count,
  status,
}: {
  senseUuid: string;
  count: number;
  status: SenseCountsState["status"];
}) {
  const [expanded, setExpanded] = useState(false);
  const label =
    status === "loading"
      ? "… Attributions"
      : status === "na"
        ? "Attributions n/a"
        : `${count} Attributions`;
  return (
    <>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: "inline-block",
          fontSize: 11,
          padding: "0 6px",
          marginLeft: 6,
          background: "var(--bg-act)",
          color: count > 0 ? "var(--t1)" : "var(--t3)",
          border: "1px solid var(--bdr)",
          borderRadius: 8,
          cursor: "pointer",
          lineHeight: "16px",
        }}
        title="Toggle prior uses of this sense"
      >
        {label}
      </button>
      {expanded && <SenseUsesPanel senseUuid={senseUuid} />}
    </>
  );
}

function useSenseRecords(senseUuids: string[]) {
  const [records, setRecords] = useState<Map<string, CoreRecordResponse>>(new Map());
  const key = senseUuids.join(",");
  useEffect(() => {
    if (senseUuids.length === 0) {
      setRecords(new Map());
      return;
    }
    let cancelled = false;
    Promise.all(
      senseUuids.map((u) =>
        getCoreRecord("senses", u).catch(() => null),
      ),
    ).then((results) => {
      if (cancelled) return;
      const next = new Map<string, CoreRecordResponse>();
      results.forEach((r, i) => {
        if (r) next.set(senseUuids[i], r);
      });
      setRecords(next);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return records;
}

function WordSensesList({
  senseUuids,
  counts,
  countsStatus,
  navigate,
  onWikilink,
}: {
  senseUuids: string[];
  counts: Record<string, number>;
  countsStatus: SenseCountsState["status"];
  navigate: Navigate;
  onWikilink: (orth: string) => void;
}) {
  const records = useSenseRecords(senseUuids);
  return (
    <ol style={{ margin: 0, paddingLeft: 18 }}>
      {senseUuids.map((u, i) => {
        const rec = records.get(u);
        if (!rec) {
          return (
            <li key={u} style={{ color: "var(--t3)" }}>
              <SenseLink uuid={u} label={`sense ${i + 1}`} />
            </li>
          );
        }
        const senseLinks = new Map<string, CoreRecordLink>(
          rec.links.map((l) => [l.target_uuid, l]),
        );
        const synUuids = asStringArray(rec.data.syntactic_function_uuids);
        const semUuids = asStringArray(rec.data.semantic_feature_uuids);
        const definition = asString(rec.data.definition);
        const source = (rec.data.source && typeof rec.data.source === "object")
          ? rec.data.source as Record<string, unknown>
          : null;
        const creator = source ? asString(source.resp) : null;
        const label = (uuid: string, fallbackCollection: string) =>
          recordLink(uuid, (v) => senseLinks.get(v), navigate, fallbackCollection);
        return (
          <li key={u} style={{ marginBottom: 4 }}>
            {synUuids.length > 0 && (
              <strong>
                {synUuids.map((s, j) => (
                  <span key={s}>
                    {j > 0 && ", "}
                    {label(s, "syntactic-functions")}
                  </span>
                ))}
              </strong>
            )}
            {semUuids.length > 0 && (
              <>
                {synUuids.length > 0 && " "}
                <em>
                  {semUuids.map((s, j) => (
                    <span key={s}>
                      {j > 0 && ", "}
                      {label(s, "semantic-features")}
                    </span>
                  ))}
                </em>
              </>
            )}
            {definition && (
              <>
                {(synUuids.length > 0 || semUuids.length > 0) && " "}
                <ProseMarkdownInline
                  text={definition}
                  onWikilink={onWikilink}
                  onCoreNav={navigate}
                />
              </>
            )}
            {creator && (
              <span style={{ color: "var(--t3)" }}> — {creator}</span>
            )}
            <AttributionsBadge
              senseUuid={u}
              count={counts[u] ?? 0}
              status={countsStatus}
            />
          </li>
        );
      })}
    </ol>
  );
}

function ProseMarkdownInline({
  text,
  onWikilink,
  onCoreNav,
}: {
  text: string;
  onWikilink: (orth: string) => void;
  onCoreNav: Navigate;
}) {
  const processed = useMemo(() => preprocessWikilinks(text || ""), [text]);
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={passthroughUrlTransform}
      components={{
        p: ({ children }) => <span>{children}</span>,
        a: ({ href, children, ...rest }) => {
          if (href?.startsWith(WIKILINK_SCHEME)) {
            const orth = decodeURIComponent(href.slice(WIKILINK_SCHEME.length));
            return (
              <a
                href={href}
                onClick={(e) => {
                  e.preventDefault();
                  onWikilink(orth);
                }}
                style={{ color: "var(--link)" }}
                {...rest}
              >
                {children}
              </a>
            );
          }
          if (href && /^[0-9a-fA-F-]{36}$/.test(href)) {
            const uuid = href;
            return (
              <a
                href={`#${uuid}`}
                onClick={(e) => {
                  e.preventDefault();
                  onCoreNav("concepts", uuid);
                }}
                style={{ color: "var(--link)" }}
              >
                {children}
              </a>
            );
          }
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--link)" }}
              {...rest}
            >
              {children}
            </a>
          );
        },
      }}
    >
      {processed}
    </ReactMarkdown>
  );
}

function WordView({
  data,
  lookup,
  navigate,
  onWikilink,
}: {
  data: Record<string, unknown>;
  lookup: Lookup;
  navigate: Navigate;
  onWikilink: (orth: string) => void;
}) {
  const superEntryUuid = asString(data.super_entry_uuid);
  const conceptUuid = asString(data.concept_uuid);
  const form = (data.form && typeof data.form === "object") ? data.form as Record<string, unknown> : null;
  const definition = asString(data.definition);
  const bibliography = asRecordArray(data.bibliography);
  const senseUuids = asStringArray(data.sense_uuids);
  const attestations = useSenseCounts(senseUuids);
  return (
    <>
      {form && (
        <FieldRow label="Form">
          <FormInline
            form={form}
            superEntryUuid={superEntryUuid}
            lookup={lookup}
            navigate={navigate}
          />
        </FieldRow>
      )}
      {conceptUuid && (
        <FieldRow label="Concept">
          {recordLink(conceptUuid, lookup, navigate, "concepts")}
        </FieldRow>
      )}
      <FieldRow label="Attestations">
        {attestations.status === "loading"
          ? "…"
          : attestations.status === "na"
            ? <span style={{ color: "var(--t3)" }}>n/a</span>
            : String(attestations.total)}
      </FieldRow>
      {definition && (
        <FieldRow label="Definition">
          <ProseMarkdown text={definition} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
      {bibliography.length > 0 && (
        <FieldRow label="Bibliography">
          <BibliographyRefList refs={bibliography} lookup={lookup} navigate={navigate} />
        </FieldRow>
      )}
      {senseUuids.length > 0 && (
        <FieldRow label="Senses">
          <WordSensesList
            senseUuids={senseUuids}
            counts={attestations.counts}
            countsStatus={attestations.status}
            navigate={navigate}
            onWikilink={onWikilink}
          />
        </FieldRow>
      )}
    </>
  );
}

function FormView({
  form,
  lookup,
  navigate,
}: {
  form: Record<string, unknown>;
  lookup: Lookup;
  navigate: Navigate;
}) {
  const orth = asString(form.orth);
  const graphUuids = asStringArray(form.graph_uuids);
  const prons = asRecordArray(form.pronunciations);
  return (
    <div>
      {orth && <div style={{ fontSize: 16 }}>{orth}</div>}
      {graphUuids.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--t3)" }}>
          graphs:{" "}
          {graphUuids.map((u, i) => (
            <span key={u}>
              {i > 0 && ", "}
              {recordLink(u, lookup, navigate, "graphs")}
            </span>
          ))}
        </div>
      )}
      {prons.length > 0 && (
        <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>
          {prons.map((p, i) => (
            <li key={i} style={{ fontSize: 12 }}>
              <code style={{ color: "var(--t3)" }}>{asString(p.lang)}</code>{" "}
              {asString(p.value)}
              {asString(p.resp) && (
                <span style={{ color: "var(--t3)" }}> ({asString(p.resp)})</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SenseView({
  data,
  lookup,
  navigate,
  onWikilink,
}: {
  data: Record<string, unknown>;
  lookup: Lookup;
  navigate: Navigate;
  onWikilink: (orth: string) => void;
}) {
  const wordUuid = asString(data.word_uuid);
  const n = asString(data.n);
  const pos = asString(data.pos);
  const syn = asStringArray(data.syntactic_function_uuids);
  const sem = asStringArray(data.semantic_feature_uuids);
  const definition = asString(data.definition);
  const usages = asRecordArray(data.usages);
  return (
    <>
      {wordUuid && (
        <FieldRow label="Word">
          {recordLink(wordUuid, lookup, navigate, "words")}
        </FieldRow>
      )}
      {pos && <FieldRow label="POS">{pos}</FieldRow>}
      {n && <FieldRow label="Attestations">{n}</FieldRow>}
      {syn.length > 0 && (
        <FieldRow label="Syntactic function">
          <UuidList uuids={syn} lookup={lookup} navigate={navigate} fallbackCollection="syntactic-functions" />
        </FieldRow>
      )}
      {sem.length > 0 && (
        <FieldRow label="Semantic feature">
          <UuidList uuids={sem} lookup={lookup} navigate={navigate} fallbackCollection="semantic-features" />
        </FieldRow>
      )}
      {definition && (
        <FieldRow label="Definition">
          <ProseMarkdown text={definition} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
      {usages.length > 0 && (
        <FieldRow label="Usages">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {usages.map((u, i) => (
              <li key={i}>
                {asString(u.type) && (
                  <span style={{ color: "var(--t3)" }}>{asString(u.type)}: </span>
                )}
                {asString(u.value)}
              </li>
            ))}
          </ul>
        </FieldRow>
      )}
    </>
  );
}

function SuperEntryView({
  data,
  lookup,
  navigate,
}: {
  data: Record<string, unknown>;
  lookup: Lookup;
  navigate: Navigate;
}) {
  const orth = asString(data.orth);
  const n = asString(data.n);
  const forms = asRecordArray(data.forms);
  const wordUuids = asStringArray(data.word_uuids);
  return (
    <>
      {orth && <FieldRow label="Orth">{orth}</FieldRow>}
      {n && <FieldRow label="n">{n}</FieldRow>}
      {forms.length > 0 && (
        <FieldRow label="Forms">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {forms.map((f, i) => (
              <li key={i} style={{ marginBottom: 4 }}>
                <FormView form={f} lookup={lookup} navigate={navigate} />
              </li>
            ))}
          </ul>
        </FieldRow>
      )}
      {wordUuids.length > 0 && (
        <FieldRow label="Words">
          <UuidList uuids={wordUuids} lookup={lookup} navigate={navigate} fallbackCollection="words" />
        </FieldRow>
      )}
    </>
  );
}

function SyntacticFunctionView({
  data,
  lookup,
  navigate,
  onWikilink,
}: {
  data: Record<string, unknown>;
  lookup: Lookup;
  navigate: Navigate;
  onWikilink: (orth: string) => void;
}) {
  const code = asString(data.code);
  const description = asString(data.description);
  const notes = asString(data.notes);
  const parents = asStringArray(data.taxonomy_parents);
  return (
    <>
      {code && <FieldRow label="Code"><code>{code}</code></FieldRow>}
      {description && (
        <FieldRow label="Description">
          <ProseMarkdown text={description} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
      {notes && (
        <FieldRow label="Notes">
          <ProseMarkdown text={notes} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
      {parents.length > 0 && (
        <FieldRow label="Taxonomy parents">
          <UuidList uuids={parents} lookup={lookup} navigate={navigate} fallbackCollection="syntactic-functions" />
        </FieldRow>
      )}
    </>
  );
}

function SemanticFeatureView({
  data,
  lookup,
  navigate,
  onWikilink,
}: {
  data: Record<string, unknown>;
  lookup: Lookup;
  navigate: Navigate;
  onWikilink: (orth: string) => void;
}) {
  const code = asString(data.code);
  const description = asString(data.description);
  const notes = asString(data.notes);
  const parents = asStringArray(data.taxonomy_parents);
  const sources = asRecordArray(data.source_references);
  return (
    <>
      {code && <FieldRow label="Code"><code>{code}</code></FieldRow>}
      {description && (
        <FieldRow label="Description">
          <ProseMarkdown text={description} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
      {notes && (
        <FieldRow label="Notes">
          <ProseMarkdown text={notes} onWikilink={onWikilink} onCoreNav={navigate} />
        </FieldRow>
      )}
      {parents.length > 0 && (
        <FieldRow label="Taxonomy parents">
          <UuidList uuids={parents} lookup={lookup} navigate={navigate} fallbackCollection="semantic-features" />
        </FieldRow>
      )}
      {sources.length > 0 && (
        <FieldRow label="Source references">
          <BibliographyRefList refs={sources} lookup={lookup} navigate={navigate} />
        </FieldRow>
      )}
    </>
  );
}

function BibliographyView({ data }: { data: Record<string, unknown> }) {
  const label = asString(data.citation_label);
  const resourceType = asString(data.resource_type);
  const titles = asRecordArray(data.titles);
  const contributors = asRecordArray(data.contributors);
  const origin = (data.origin && typeof data.origin === "object") ? data.origin as Record<string, unknown> : null;
  const notes = asRecordArray(data.notes);
  return (
    <>
      {label && <FieldRow label="Citation label">{label}</FieldRow>}
      {resourceType && <FieldRow label="Type">{resourceType}</FieldRow>}
      {titles.length > 0 && (
        <FieldRow label="Title">
          {titles.map((t, i) => (
            <div key={i}>{asString(t.title)}</div>
          ))}
        </FieldRow>
      )}
      {contributors.length > 0 && (
        <FieldRow label="Contributors">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {contributors.map((c, i) => {
              const family = asString(c.family);
              const given = asString(c.given);
              const roles = asStringArray(c.roles);
              return (
                <li key={i}>
                  {family} {given}
                  {roles.length > 0 && (
                    <span style={{ color: "var(--t3)" }}> — {roles.join(", ")}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </FieldRow>
      )}
      {origin && (
        <FieldRow label="Origin">
          {asString(origin.publisher)}
          {asString(origin.place) && `, ${asString(origin.place)}`}
          {asString(origin.date_issued) && ` (${asString(origin.date_issued)})`}
        </FieldRow>
      )}
      {notes.length > 0 && (
        <FieldRow label="Notes">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {notes.map((n, i) => (
              <li key={i}>{asString(n.text)}</li>
            ))}
          </ul>
        </FieldRow>
      )}
    </>
  );
}

function GraphView({ data }: { data: Record<string, unknown> }) {
  return (
    <pre
      style={{
        fontSize: 11,
        background: "var(--bg-1)",
        padding: 8,
        border: "1px solid var(--bd)",
        borderRadius: 3,
        overflow: "auto",
        color: "var(--t1)",
        maxHeight: 480,
      }}
    >
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function FallbackView({ data }: { data: Record<string, unknown> }) {
  return (
    <pre
      style={{
        fontSize: 11,
        background: "var(--bg-1)",
        padding: 8,
        border: "1px solid var(--bd)",
        borderRadius: 3,
        overflow: "auto",
        color: "var(--t1)",
      }}
    >
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// ---------- main component --------------------------------------------------

export function CoreRecord({
  paneId,
  tabId,
  collection,
  uuid,
}: {
  paneId: string;
  tabId: string;
  collection: string;
  uuid: string;
}) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [showRaw, setShowRaw] = useState(false);
  const [editing, setEditing] = useState(false);
  const [conceptWords, setConceptWords] = useState<CoreConceptWord[] | null>(null);
  const [backlinks, setBacklinks] = useState<CoreBacklinksResponse | null>(null);
  const historyLen = useWorkspace((s) => {
    const tab = findTab(s.pane, paneId, tabId);
    return tab?.type === "core-record" ? tab.history?.length ?? 0 : 0;
  });
  const authenticated = useWorkspace((s) => s.auth.status === "authenticated");
  const isEditor = useWorkspace((s) => s.auth.session?.user?.is_editor ?? false);
  const [deleted, setDeleted] = useState<{ compareUrl: string; prUrl: string | null } | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getCoreRecord(collection, uuid)
      .then((record) => {
        if (!cancelled) setState({ status: "ok", record });
      })
      .catch((e) => {
        if (!cancelled) setState({ status: "error", error: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [collection, uuid]);

  useEffect(() => {
    setBacklinks(null);
    if (collection === "concepts" || collection === "words") {
      return;
    }
    let cancelled = false;
    getCoreBacklinks(collection, uuid)
      .then((r) => {
        if (!cancelled) setBacklinks(r);
      })
      .catch(() => {
        if (!cancelled) setBacklinks({ uuid, total: 0, groups: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [collection, uuid]);

  useEffect(() => {
    if (collection !== "concepts") {
      setConceptWords(null);
      return;
    }
    let cancelled = false;
    setConceptWords(null);
    getCoreConceptWords(uuid)
      .then((r) => {
        if (!cancelled) setConceptWords(r.words);
      })
      .catch(() => {
        if (!cancelled) setConceptWords([]);
      });
    return () => {
      cancelled = true;
    };
  }, [collection, uuid]);

  if (state.status === "loading") {
    return <div className="empty-pane">Loading core record…</div>;
  }
  if (state.status === "error") {
    return <div className="empty-pane">Failed to load record: {state.error}</div>;
  }

  const record = state.record;
  const linksByUuid = new Map<string, CoreRecordLink>(
    record.links.map((l) => [l.target_uuid, l]),
  );
  const lookup: Lookup = (u) => linksByUuid.get(u);

  const navigate: Navigate = (nextCollection, nextUuid) => {
    workspace.replaceCoreRecord(paneId, tabId, nextCollection, nextUuid);
  };

  const handleWikilink = (orth: string) => {
    getCoreSuperEntryByOrth(orth)
      .then((r) => navigate("super-entries", r.uuid))
      .catch(() => {
        // No super-entry exists for this orth; silently ignore.
      });
  };

  const body = renderTypedView(record, lookup, navigate, handleWikilink);

  return (
    <div
      className="core-record"
      style={{
        overflow: "auto",
        padding: "12px 16px",
        userSelect: "text",
        WebkitUserSelect: "text",
        cursor: "text",
      }}
      onMouseUp={() => {
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed) return;
        const text = sel.toString().trim();
        if (text) workspace.setSearchQuery(text);
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <button
          type="button"
          onClick={() => workspace.coreRecordBack(paneId, tabId)}
          disabled={historyLen === 0}
          title={historyLen === 0 ? "No previous record" : "Back"}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "var(--bg-1)",
            color: historyLen === 0 ? "var(--t3)" : "var(--t1)",
            border: "1px solid var(--bd)",
            borderRadius: 3,
            cursor: historyLen === 0 ? "default" : "pointer",
          }}
        >
          ← Back
        </button>
        <h2 style={{ fontSize: 16, margin: 0, color: "var(--t1)" }}>
          {record.display_label}
        </h2>
        {authenticated && (
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            style={{
              marginLeft: "auto",
              fontSize: 11,
              padding: "2px 8px",
              background: editing ? "var(--accent, #2a5)" : "var(--bg-1)",
              color: editing ? "white" : "var(--t1)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
              cursor: "pointer",
            }}
            title={editing ? "Hide editor" : "Edit on your fork"}
          >
            {editing ? "Editing" : "Edit"}
          </button>
        )}
        {isEditor && (
          <button
            type="button"
            disabled={deleting || deleted !== null}
            onClick={async () => {
              const label = record.display_label || record.uuid;
              if (!window.confirm(
                `Delete ${record.collection}/${label}? ` +
                "This commits a deletion to your fork branch.",
              )) return;
              setDeleting(true);
              try {
                const resp = await deleteCoreRecord(record.collection, record.uuid);
                setDeleted({ compareUrl: resp.compare_url, prUrl: resp.pr_url });
              } catch (e) {
                window.alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
              } finally {
                setDeleting(false);
              }
            }}
            style={{
              marginLeft: authenticated ? 4 : "auto",
              fontSize: 11,
              padding: "2px 8px",
              background: "var(--bg-1)",
              color: "var(--t1)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
              cursor: deleting || deleted !== null ? "default" : "pointer",
            }}
            title="Delete this record on your fork"
          >
            {deleting ? "…" : "×"}
          </button>
        )}
      </div>
      {deleted && (
        <div
          style={{
            fontSize: 12,
            marginBottom: 12,
            padding: 8,
            background: "var(--bg-1)",
            border: "1px solid var(--bd)",
            borderRadius: 3,
            color: "var(--t1)",
          }}
        >
          Deleted on your fork.{" "}
          <a href={deleted.compareUrl} target="_blank" rel="noreferrer" style={{ color: "var(--link)" }}>
            View diff
          </a>
          {deleted.prUrl && (
            <>
              {" · "}
              <a href={deleted.prUrl} target="_blank" rel="noreferrer" style={{ color: "var(--link)" }}>
                Open PR
              </a>
            </>
          )}
        </div>
      )}
      <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 12 }}>
        {record.collection} · {record.uuid}
      </div>

      <details
        open={showRaw}
        onToggle={(e) => setShowRaw((e.target as HTMLDetailsElement).open)}
        style={{ marginBottom: 12 }}
      >
        <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--t2)" }}>
          Raw YAML data
        </summary>
        <pre
          style={{
            fontSize: 11,
            background: "var(--bg-1)",
            padding: 8,
            border: "1px solid var(--bd)",
            borderRadius: 3,
            overflow: "auto",
            color: "var(--t1)",
            maxHeight: 320,
          }}
        >
          {JSON.stringify(record.data, null, 2)}
        </pre>
      </details>

      <div className="core-record-body" style={{ fontSize: 13, lineHeight: 1.55 }}>
        {body}

        {collection === "concepts" && conceptWords != null && conceptWords.length > 0 && (
          <FieldRow label="Words">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {conceptWords.map((w) => (
                <li key={w.uuid}>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      navigate("words", w.uuid);
                    }}
                    style={{ color: "var(--link)" }}
                  >
                    {w.super_entry_orth ?? w.display_label ?? w.uuid}
                  </a>
                  {w.n != null && (
                    <span style={{ color: "var(--t3)" }}> · n={w.n}</span>
                  )}
                </li>
              ))}
            </ul>
          </FieldRow>
        )}
      </div>

      {editing && (
        <CoreRecordEditor
          record={record}
          onClose={() => setEditing(false)}
          onSaved={(nextData) => {
            setState({ status: "ok", record: { ...record, data: nextData } });
          }}
        />
      )}

      {backlinks &&
        backlinks.total > 0 &&
        record.collection !== "concepts" &&
        record.collection !== "words" && (
          <BacklinksSection backlinks={backlinks} onOpen={navigate} />
        )}
    </div>
  );
}

function renderTypedView(
  record: CoreRecordResponse,
  lookup: Lookup,
  navigate: Navigate,
  onWikilink: (orth: string) => void,
): ReactNode {
  switch (record.type) {
    case "concept":
      return <ConceptView data={record.data} lookup={lookup} navigate={navigate} onWikilink={onWikilink} />;
    case "word":
      return <WordView data={record.data} lookup={lookup} navigate={navigate} onWikilink={onWikilink} />;
    case "sense":
      return <SenseView data={record.data} lookup={lookup} navigate={navigate} onWikilink={onWikilink} />;
    case "super-entry":
      return <SuperEntryView data={record.data} lookup={lookup} navigate={navigate} />;
    case "syntactic-function":
      return <SyntacticFunctionView data={record.data} lookup={lookup} navigate={navigate} onWikilink={onWikilink} />;
    case "semantic-feature":
      return <SemanticFeatureView data={record.data} lookup={lookup} navigate={navigate} onWikilink={onWikilink} />;
    case "bibliography":
      return <BibliographyView data={record.data} />;
    case "graph":
      return <GraphView data={record.data} />;
    default:
      return <FallbackView data={record.data} />;
  }
}

const COLLECTION_TITLES: Record<string, string> = {
  concepts: "Concepts",
  graphs: "Graphs",
  "syntactic-functions": "Syntactic functions",
  "semantic-features": "Semantic features",
  bibliography: "Bibliography",
  words: "Words",
  senses: "Senses",
  "super-entries": "Super-entries",
};

function BacklinksSection({
  backlinks,
  onOpen,
}: {
  backlinks: CoreBacklinksResponse;
  onOpen: (collection: string, uuid: string) => void;
}) {
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  return (
    <div style={{ marginBottom: 12, fontSize: 12 }}>
      <div style={{ color: "var(--t2)", marginBottom: 4 }}>
        Referenced by ({backlinks.total})
      </div>
      {backlinks.groups.map((g) => {
        const isOpen = !!openGroups[g.collection];
        const label = COLLECTION_TITLES[g.collection] ?? g.collection;
        return (
          <div key={g.collection}>
            <div
              className="cat-sub"
              onClick={() =>
                setOpenGroups((p) => ({ ...p, [g.collection]: !p[g.collection] }))
              }
              title={label}
            >
              <span className="cat-caret">{isOpen ? "▾" : "▸"}</span>
              <span className="cat-zh">{label}</span>
              <span className="cat-count">{g.total}</span>
            </div>
            {isOpen && (
              <ul style={{ margin: "2px 0 6px 0", paddingLeft: 28 }}>
                {g.items.map((it) => (
                  <li key={it.uuid}>
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        onOpen(it.collection, it.uuid);
                      }}
                      style={{ color: "var(--link)" }}
                    >
                      {it.display_label}
                    </a>
                    {it.relation && (
                      <span style={{ color: "var(--t3)" }}> — {it.relation}</span>
                    )}
                  </li>
                ))}
                {g.total > g.items.length && (
                  <li style={{ color: "var(--t3)" }}>
                    … {g.total - g.items.length} more
                  </li>
                )}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
