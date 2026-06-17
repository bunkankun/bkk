import { useEffect, useState } from "react";
import type {
  ParallelCluster,
  ParallelLocation,
  ParallelSearchResponse,
  SearchFacetValue,
  SearchFacets,
  SearchHit,
  SearchOverview,
  TranslationSearchFacets,
  TranslationSearchResponse,
  TranslationSegmentHit,
  TranslationSummary,
} from "../../api/types";
import { listColor, listPathFromName } from "../../lib/textLists";
import { krClass } from "../../lib/krClass";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import type {
  ListFilterMode,
  SearchFacetKind,
  SearchFilters,
  TextList,
  TranslationSearchFilters,
} from "../../state/useWorkspace";

const PAGE_SIZE = 50;
const DEFAULT_FACET_LIMIT = 12;
const EXPANDED_FACET_LIMIT = 100;
const DATE_FILTER_DELAY_MS = 1500;

// Treat sentence-enders as "line" boundaries for KWIC truncation. Same set
// the read-mode phrase splitter uses (TextViewer.tsx PHRASE_END_RE).
const PHRASE_END_RE = /[。！？；]/;

// Trim the left context (text before the match) so it begins at the start
// of a "line": after the most recent phrase-ending punctuation. Falls back
// to a hard char cap when there's no phrase boundary in the left context.
function trimLeftContext(s: string, maxChars = 32): string {
  let cut = -1;
  for (let i = 0; i < s.length; i++) {
    if (PHRASE_END_RE.test(s[i])) cut = i;
  }
  // Prefer phrase-boundary trim; if it leaves nothing, fall through to cap.
  if (cut >= 0 && cut < s.length - 1) return s.slice(cut + 1);
  if (s.length <= maxChars) return s;
  return s.slice(s.length - maxChars);
}

// Trim the right context (text after the match) so it ends at the end of a
// "line": at the next phrase-ending punctuation (inclusive).
function trimRightContext(s: string, maxChars = 32): string {
  for (let i = 0; i < s.length; i++) {
    if (PHRASE_END_RE.test(s[i])) return s.slice(0, i + 1);
  }
  if (s.length <= maxChars) return s;
  return s.slice(0, maxChars);
}

// Witness-line collapse: when the variant-interior portion (between the
// master anchor and the match) is longer than COLLAPSE_THRESHOLD, hide the
// bulk and keep WITNESS_NEAR_CHARS adjacent to the match. The hidden text
// is exposed via the chip's title attribute for hover preview.
const WITNESS_NEAR_CHARS = 4;
const COLLAPSE_THRESHOLD = 6;

const EMPTY_FACETS: SearchFacets = {
  textid: [],
  category: [],
  witness: [],
  voice: [],
  left_char: [],
  right_char: [],
  left_bigram: [],
  right_bigram: [],
  around_binom: [],
  date: {},
};

type FacetGroupKey = SearchFacetKind | "textid";

function facetTitle(v: SearchFacetValue): string {
  return v.label ?? v.value;
}

function normalizeFacets(facets: SearchFacets | undefined): SearchFacets {
  return {
    textid: facets?.textid ?? [],
    category: facets?.category ?? [],
    witness: facets?.witness ?? [],
    voice: facets?.voice ?? [],
    left_char: facets?.left_char ?? [],
    right_char: facets?.right_char ?? [],
    left_bigram: facets?.left_bigram ?? [],
    right_bigram: facets?.right_bigram ?? [],
    around_binom: facets?.around_binom ?? [],
    date: facets?.date ?? {},
  };
}

function hasAnyFilter(filters: SearchFilters): boolean {
  return Boolean(
    filters.textid ||
      filters.textidExclude.length ||
      filters.category.length ||
      filters.categoryExclude.length ||
      filters.dateBefore != null ||
      filters.dateAfter != null ||
      filters.witness.length ||
      filters.witnessExclude.length ||
      filters.voice.length ||
      filters.voiceExclude.length ||
      filters.leftChar.length ||
      filters.leftCharExclude.length ||
      filters.rightChar.length ||
      filters.rightCharExclude.length ||
      filters.leftBigram.length ||
      filters.leftBigramExclude.length ||
      filters.rightBigram.length ||
      filters.rightBigramExclude.length ||
      filters.aroundBinom.length ||
      filters.aroundBinomExclude.length,
  );
}

function FacetGroup({
  label,
  values,
  kind,
  disabled,
  expanded,
  hasMore,
  onMore,
  masterOnly,
}: {
  label: string;
  values: SearchFacetValue[];
  kind: SearchFacetKind;
  disabled: boolean;
  expanded: boolean;
  hasMore: boolean;
  onMore: () => void;
  masterOnly?: boolean;
}) {
  if (values.length === 0) return null;
  const shown = expanded ? values : values.slice(0, DEFAULT_FACET_LIMIT);
  return (
    <div className="kwic-facet-group">
      <div className="kwic-facet-label">{label}</div>
      <div className="kwic-facet-values">
        {shown.map((v) => {
          const isMasterToggle = kind === "witness" && v.value === "master";
          const selected = isMasterToggle ? !!masterOnly : v.selected;
          const onClick = isMasterToggle
            ? () => workspace.setMasterOnly(!masterOnly)
            : (e: React.MouseEvent) =>
                void workspace.toggleSearchFacet(kind, v.value, e.ctrlKey ? "exclude" : "include");
          return (
            <button
              key={v.value}
              type="button"
              className={`kwic-facet-chip${selected ? " on" : ""}${v.excluded ? " off" : ""}`}
              disabled={disabled}
              onClick={onClick}
              title={isMasterToggle ? "Show only master-edition matches" : facetTitle(v)}
            >
              <span className={`kwic-facet-value ${krClass(v.value)}`}>{v.value}</span>
              <span className="kwic-facet-count">{v.count}</span>
            </button>
          );
        })}
        {!expanded && hasMore ? (
          <button
            type="button"
            className="kwic-facet-more"
            disabled={disabled}
            onClick={onMore}
          >
            ...
          </button>
        ) : null}
      </div>
    </div>
  );
}

function TextFacetGroup({
  values,
  selected,
  disabled,
  expanded,
  hasMore,
  onMore,
}: {
  values: SearchFacetValue[];
  selected: string | null;
  disabled: boolean;
  expanded: boolean;
  hasMore: boolean;
  onMore: () => void;
}) {
  if (values.length === 0) return null;
  const shown = expanded ? values : values.slice(0, DEFAULT_FACET_LIMIT);
  return (
    <div className="kwic-facet-group">
      <div className="kwic-facet-label">Text</div>
      <div className="kwic-facet-values">
        {shown.map((v) => (
          <button
            key={v.value}
            type="button"
            className={`kwic-facet-chip${v.value === selected ? " on" : ""}${v.excluded ? " off" : ""}`}
            disabled={disabled}
            onClick={(e) => {
              if (e.ctrlKey) void workspace.toggleSearchTextidExclude(v.value);
              else void workspace.setSearchTextid(v.value === selected ? null : v.value);
            }}
            title={facetTitle(v)}
          >
            <span className={`kwic-facet-value ${krClass(v.value)}`}>{v.value}</span>
            <span className="kwic-facet-count">{v.count}</span>
          </button>
        ))}
        {!expanded && hasMore ? (
          <button
            type="button"
            className="kwic-facet-more"
            disabled={disabled}
            onClick={onMore}
          >
            ...
          </button>
        ) : null}
      </div>
    </div>
  );
}

function ListFacetGroup({
  lists,
  mode,
  disabled,
}: {
  lists: TextList[];
  mode: ListFilterMode;
  disabled: boolean;
}) {
  if (lists.length === 0 && mode === "off") return null;
  return (
    <div className="kwic-facet-group">
      <div className="kwic-facet-label">Lists</div>
      <div className="kwic-list-facet">
        {lists.length > 0 ? (
          <div className="kwic-facet-values">
            {lists.map((list) => (
              <button
                key={list.path}
                type="button"
                className={`kwic-list-filter-badge${mode !== "off" ? " on" : ""}`}
                style={{ backgroundColor: listColor(list.path) }}
                disabled={disabled}
                onClick={() => void workspace.setListFilterMode(mode === "off" ? "any" : "off")}
                title={`${list.name} · ${list.textids.length} texts`}
              >
                <span>{list.name}</span>
                <span>{list.textids.length}</span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="kwic-list-mode">
          {(["off", "any", "all"] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={mode === value ? "on" : ""}
              disabled={disabled}
              onClick={() => void workspace.setListFilterMode(value)}
            >
              {value === "off" ? "badges" : value}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function DateFacet({
  before,
  after,
  currentTextid,
  currentDate,
  min,
  max,
  disabled,
}: {
  before: number | null;
  after: number | null;
  currentTextid?: string | null;
  currentDate?: number | null;
  min?: number | null;
  max?: number | null;
  disabled: boolean;
}) {
  const [beforeDraft, setBeforeDraft] = useState(before == null ? "" : String(before));
  const [afterDraft, setAfterDraft] = useState(after == null ? "" : String(after));

  useEffect(() => {
    setBeforeDraft(before == null ? "" : String(before));
  }, [before]);

  useEffect(() => {
    setAfterDraft(after == null ? "" : String(after));
  }, [after]);

  const commitYear = (which: "before" | "after", raw: string) => {
    const trimmed = raw.trim();
    const value = trimmed === "" ? null : Number(trimmed);
    if (value == null || Number.isFinite(value)) {
      void workspace.setSearchDateFilter(which, value);
    }
  };

  useEffect(() => {
    if (beforeDraft === (before == null ? "" : String(before))) return;
    const timer = window.setTimeout(() => {
      commitYear("before", beforeDraft);
    }, DATE_FILTER_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [beforeDraft, before]);

  useEffect(() => {
    if (afterDraft === (after == null ? "" : String(after))) return;
    const timer = window.setTimeout(() => {
      commitYear("after", afterDraft);
    }, DATE_FILTER_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [afterDraft, after]);

  return (
    <div className="kwic-facet-group">
      <div className="kwic-facet-label">Date</div>
      <div className="kwic-date-row">
        <label>
          <span>&lt;</span>
          <input
            type="number"
            value={beforeDraft}
            disabled={disabled}
            placeholder={max != null ? String(max) : "year"}
            onChange={(e) => setBeforeDraft(e.target.value)}
            onBlur={() => commitYear("before", beforeDraft)}
          />
        </label>
        <label>
          <span>&gt;</span>
          <input
            type="number"
            value={afterDraft}
            disabled={disabled}
            placeholder={min != null ? String(min) : "year"}
            onChange={(e) => setAfterDraft(e.target.value)}
            onBlur={() => commitYear("after", afterDraft)}
          />
        </label>
      </div>
      {currentTextid && currentDate != null ? (
        <div className="kwic-date-pivots">
          <button
            type="button"
            disabled={disabled}
            onClick={() => void workspace.setSearchDateFilter("before", currentDate)}
            title={`Before ${currentTextid} (${currentDate})`}
          >
            before current
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => void workspace.setSearchDateFilter("after", currentDate)}
            title={`After ${currentTextid} (${currentDate})`}
          >
            after current
          </button>
        </div>
      ) : null}
    </div>
  );
}

function HitRow({ hit }: { hit: SearchHit }) {
  const witness = hit.matched_via !== "master" ? hit.matched_via : null;
  const left = trimLeftContext(hit.left);
  const right = trimRightContext(hit.right);
  const leftElided = left.length < hit.left.length;
  const rightElided = right.length < hit.right.length;
  const wLeftRaw = hit.witness_left ?? "";
  const wRightRaw = hit.witness_right ?? "";
  const showWitnessLine = witness !== null && (wLeftRaw.length > 0 || wRightRaw.length > 0);
  const badges = workspace.listBadgesForTextid(hit.textid);
  // Split each side into anchor (master/identity, shared with master line)
  // and interior (variant chars). Trim only the anchor with the master-line
  // trim helpers; collapse the interior when long.
  const wLeftVarOff = Math.max(0, Math.min(wLeftRaw.length, hit.witness_left_variant_offset ?? 0));
  const wRightVarEnd = Math.max(0, Math.min(wRightRaw.length, hit.witness_right_variant_end ?? 0));
  const wLeftAnchorRaw = wLeftRaw.slice(0, wLeftVarOff);
  const wLeftInterior = wLeftRaw.slice(wLeftVarOff);
  const wLeftAnchor = trimLeftContext(wLeftAnchorRaw);
  const wLeftAnchorElided = wLeftAnchor.length < wLeftAnchorRaw.length;
  const wLeftCollapse = wLeftInterior.length > COLLAPSE_THRESHOLD;
  const wLeftNear = wLeftCollapse ? wLeftInterior.slice(-WITNESS_NEAR_CHARS) : wLeftInterior;
  const wLeftHidden = wLeftCollapse ? wLeftInterior.slice(0, -WITNESS_NEAR_CHARS) : "";
  const wRightInterior = wRightRaw.slice(0, wRightVarEnd);
  const wRightAnchorRaw = wRightRaw.slice(wRightVarEnd);
  const wRightAnchor = trimRightContext(wRightAnchorRaw);
  const wRightAnchorElided = wRightAnchor.length < wRightAnchorRaw.length;
  const wRightCollapse = wRightInterior.length > COLLAPSE_THRESHOLD;
  const wRightNear = wRightCollapse ? wRightInterior.slice(0, WITNESS_NEAR_CHARS) : wRightInterior;
  const wRightHidden = wRightCollapse ? wRightInterior.slice(WITNESS_NEAR_CHARS) : "";
  return (
    <button
      type="button"
      className="kwic-row"
      onClick={() => workspace.openHit(hit)}
      title={`${hit.textid} · juan ${hit.juan_seq} · ${hit.bucket} @${hit.master_offset}`}
    >
      <div className="kwic-meta">
        {hit.toc_label ? <span className="kwic-label">{hit.toc_label}</span> : null}
        <span className={`kwic-textid ${krClass(hit.textid)}`}>{hit.textid}</span>
        <span className="kwic-juan">juan {hit.juan_seq}</span>
        {hit.bucket !== "body" ? <span className="kwic-chip">{hit.bucket}</span> : null}
        {witness ? <span className="kwic-chip">{witness}</span> : null}
        {badges.map((badge) => (
          <span
            key={badge.path}
            className="kwic-list-badge"
            style={{ backgroundColor: badge.color }}
            title={badge.name}
          >
            {badge.name}
          </span>
        ))}
      </div>
      <div className="kwic-line">
        <span className="kwic-left">
          {leftElided ? <span className="kwic-ell">…</span> : null}
          {left}
        </span>
        <mark className="kwic-match">{hit.match}</mark>
        <span className="kwic-right">
          {right}
          {rightElided ? <span className="kwic-ell">…</span> : null}
        </span>
      </div>
      {showWitnessLine && (
        <div className="kwic-line kwic-line-witness">
          <span className="kwic-left">
            {wLeftAnchorElided ? <span className="kwic-ell">…</span> : null}
            {wLeftAnchor}
            {wLeftCollapse ? (
              <span className="kwic-fold" title={wLeftHidden}>⟨…⟩</span>
            ) : null}
            {wLeftNear}
          </span>
          <mark className="kwic-match">{hit.matched_text}</mark>
          <span className="kwic-right">
            {wRightNear}
            {wRightCollapse ? (
              <span className="kwic-fold" title={wRightHidden}>⟨…⟩</span>
            ) : null}
            {wRightAnchor}
            {wRightAnchorElided ? <span className="kwic-ell">…</span> : null}
          </span>
        </div>
      )}
    </button>
  );
}

function highlightQuery(text: string, query: string) {
  if (!query.trim()) return text;
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase()
      ? <mark key={i} className="kwic-match">{part}</mark>
      : part
  );
}

function hitToSummary(hit: TranslationSegmentHit): TranslationSummary {
  return {
    id: hit.bundle_id,
    source_textid: hit.source_textid,
    language: hit.language ?? null,
    title: hit.title ?? null,
    responsibility: hit.responsibility,
    date: hit.date ?? null,
    juan_count: 0,
    segment_count: 0,
    source_juans: [],
  };
}

function TranslationHitRow({ hit, query }: { hit: TranslationSegmentHit; query: string }) {
  const names = hit.responsibility.map((r) => r.name).filter(Boolean).join(", ");
  const onClick = () => {
    workspace.openTranslationHit(hitToSummary(hit), hit.juan_seq, hit.corresp, hit.source_text ?? null);
  };
  return (
    <button type="button" className={`kwic-row${hit.is_ai ? " kwic-row-ai" : ""}`} onClick={onClick}>
      <div className="kwic-meta">
        <span className={`kwic-textid ${krClass(hit.source_textid)}`}>{hit.source_textid}</span>
        <span className="kwic-juan">juan {hit.juan_seq}</span>
        {hit.language ? <span className="kwic-chip">{hit.language}</span> : null}
        {names ? <span className="kwic-label">{names}</span> : null}
        {hit.date ? <span className="kwic-label">{hit.date.slice(0, 4)}</span> : null}
      </div>
      {hit.source_text ? (
        <div className="kwic-line kwic-line-source kwic-line-trans">{hit.source_text}</div>
      ) : null}
      <div className="kwic-line kwic-line-trans">{highlightQuery(hit.text, query)}</div>
    </button>
  );
}

function TranslationFacetChip({
  v,
  active,
  disabled,
  onClick,
}: {
  v: SearchFacetValue;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`kwic-facet-chip${active ? " on" : ""}`}
      disabled={disabled}
      onClick={onClick}
      title={v.label ?? v.value}
    >
      <span className="kwic-facet-value">{v.value}</span>
      <span className="kwic-facet-count">{v.count}</span>
    </button>
  );
}

function TranslationFacets({
  facets,
  filters,
  disabled,
}: {
  facets: TranslationSearchFacets;
  filters: TranslationSearchFilters;
  disabled: boolean;
}) {
  return (
    <div className="kwic-facets">
      {facets.language.length > 0 && (
        <div className="kwic-facet-group">
          <div className="kwic-facet-label">Language</div>
          <div className="kwic-facet-values">
            {facets.language.map((v) => (
              <TranslationFacetChip
                key={v.value}
                v={v}
                active={filters.lang === v.value}
                disabled={disabled}
                onClick={() =>
                  void workspace.setTranslationFilter("lang", filters.lang === v.value ? null : v.value)
                }
              />
            ))}
          </div>
        </div>
      )}
      {facets.category.length > 0 && (
        <div className="kwic-facet-group">
          <div className="kwic-facet-label">Category</div>
          <div className="kwic-facet-values">
            {facets.category.map((v) => (
              <TranslationFacetChip
                key={v.value}
                v={v}
                active={filters.category === v.value}
                disabled={disabled}
                onClick={() =>
                  void workspace.setTranslationFilter("category", filters.category === v.value ? null : v.value)
                }
              />
            ))}
          </div>
        </div>
      )}
      {facets.type.length > 1 && (
        <div className="kwic-facet-group">
          <div className="kwic-facet-label">Type</div>
          <div className="kwic-facet-values">
            {facets.type.map((v) => (
              <TranslationFacetChip
                key={v.value}
                v={v}
                active={filters.type === v.value}
                disabled={disabled}
                onClick={() =>
                  void workspace.setTranslationFilter("type", filters.type === v.value ? null : v.value as "AI" | "human")
                }
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TranslationResultsView({
  response,
  query,
  filters,
  status,
}: {
  response: TranslationSearchResponse;
  query: string;
  filters: TranslationSearchFilters;
  status: string;
}) {
  const disabled = status === "loading";
  const hasPrev = response.offset > 0;
  const hasNext = response.offset + response.limit < response.total;
  const start = response.offset + 1;
  const end = Math.min(response.offset + response.limit, response.total);
  const hasFilters = filters.lang != null || filters.category != null ||
    filters.type != null || filters.dateBefore != null || filters.dateAfter != null;

  return (
    <div className="rc kwic-list">
      <div className="kwic-summary">
        <span>
          {start}–{end} of {response.total} for "{response.q}"
        </span>
        {hasFilters ? (
          <button
            type="button"
            className="kwic-clear-filters"
            disabled={disabled}
            onClick={() => {
              void workspace.setTranslationFilter("lang", null);
              void workspace.setTranslationFilter("category", null);
              void workspace.setTranslationFilter("type", null);
              void workspace.setTranslationFilter("dateBefore", null);
              void workspace.setTranslationFilter("dateAfter", null);
            }}
          >
            clear facets
          </button>
        ) : null}
      </div>
      <TranslationFacets facets={response.facets} filters={filters} disabled={disabled} />
      {response.hits.map((hit, i) => (
        <TranslationHitRow
          key={`${hit.bundle_id}:${hit.juan_seq}:${hit.corresp ?? i}`}
          hit={hit}
          query={query}
        />
      ))}
      {(hasPrev || hasNext) && (
        <div className="kwic-pager">
          <button
            type="button"
            disabled={!hasPrev || disabled}
            onClick={() => void workspace.runSearchAt(Math.max(0, response.offset - PAGE_SIZE))}
          >
            ← Prev
          </button>
          <button
            type="button"
            disabled={!hasNext || disabled}
            onClick={() => void workspace.runSearchAt(response.offset + PAGE_SIZE)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function ParallelLocationRow({ loc, marker }: { loc: ParallelLocation; marker: string }) {
  const onClick = () =>
    workspace.openContributionLocation({
      textid: loc.textid,
      seq: loc.juan_seq,
      bucket: loc.bucket,
      masterOffset: loc.start,
      length: loc.end - loc.start,
    });
  return (
    <button
      type="button"
      className="kwic-row"
      onClick={onClick}
      title={`${loc.textid} · juan ${loc.juan_seq} · ${loc.bucket} @${loc.start}`}
    >
      <div className="kwic-meta">
        {loc.toc_label ? <span className="kwic-label">{loc.toc_label}</span> : null}
        <span className={`kwic-textid ${krClass(loc.textid)}`}>{loc.textid}</span>
        <span className="kwic-juan">juan {loc.juan_seq}</span>
        {loc.bucket !== "body" ? <span className="kwic-chip">{loc.bucket}</span> : null}
        {loc.edit_distance > 0 ? (
          <span className="kwic-chip" title="Edit distance from cluster representative">
            Δ{loc.edit_distance}
          </span>
        ) : null}
      </div>
      <div className="kwic-line">
        <span className="kwic-left">{loc.left}</span>
        <mark className="kwic-match">{marker}</mark>
        <span className="kwic-right">{loc.right}</span>
      </div>
    </button>
  );
}

const PARALLEL_TEXT_MAX = 200;

function parallelMarker(text: string): string {
  const chars = Array.from(text);
  if (chars.length <= 4) return text;
  return `${chars.slice(0, 2).join("")}…${chars.slice(-2).join("")}`;
}

function ParallelClusterRow({ cluster }: { cluster: ParallelCluster }) {
  const elided = cluster.text.length > PARALLEL_TEXT_MAX;
  const shown = elided ? cluster.text.slice(0, PARALLEL_TEXT_MAX) : cluster.text;
  const marker = parallelMarker(cluster.text);
  return (
    <div className="parallel-cluster">
      <div className="kwic-summary">
        <span>{cluster.cluster_id}</span>
        <span className="kwic-sort">· {cluster.length} chars</span>
        <span className="kwic-sort">· {cluster.occurrence_count} occurrences</span>
        {cluster.representative_edits > 0 ? (
          <span className="kwic-sort" title="Max edit distance among occurrences in this cluster">
            · ≤Δ{cluster.representative_edits}
          </span>
        ) : null}
      </div>
      <div className="parallel-text" title={elided ? cluster.text : undefined}>
        {shown}
        {elided ? <span className="kwic-ell">…</span> : null}
      </div>
      {cluster.locations.map((loc, i) => (
        <ParallelLocationRow
          key={`${loc.textid}:${loc.juan_seq}:${loc.bucket_id}:${loc.start}:${i}`}
          loc={loc}
          marker={marker}
        />
      ))}
    </div>
  );
}

function ParallelResultsView({
  response,
  query,
  status,
}: {
  response: ParallelSearchResponse;
  query: string;
  status: string;
}) {
  const disabled = status === "loading";
  const start = response.offset + 1;
  const end = Math.min(response.offset + response.limit, response.total);
  const hasPrev = response.offset > 0;
  const hasNext = response.offset + response.limit < response.total;
  const seed = query.trim();
  const setSort = (sort: "frequency" | "length") => {
    if (response.sort === sort) return;
    workspace.setParallelOption("sort", sort);
    void workspace.runSearchAt(0);
  };
  return (
    <div className="rc kwic-list">
      <div className="kwic-summary">
        <span>
          {start}–{end} of {response.total} clusters for "{seed}"
        </span>
        <span className="kwic-sort">· {response.bucket}</span>
        <span className="kwic-sort">· min {response.min_length} chars</span>
        <span className="kwic-sort">· ≥{response.min_occurrences}×</span>
        {response.max_edits > 0 ? (
          <span className="kwic-sort" title="Maximum edits per occurrence vs. the cluster representative">
            · Δ≤{response.max_edits}
          </span>
        ) : null}
        <button
          type="button"
          className={`kwic-facet-chip${response.sort === "frequency" ? " on" : ""}`}
          disabled={disabled}
          onClick={() => setSort("frequency")}
          title="Most frequent first"
        >
          most frequent
        </button>
        <button
          type="button"
          className={`kwic-facet-chip${response.sort === "length" ? " on" : ""}`}
          disabled={disabled}
          onClick={() => setSort("length")}
          title="Longest first"
        >
          longest
        </button>
      </div>
      {response.clusters.map((c) => (
        <ParallelClusterRow key={c.cluster_id} cluster={c} />
      ))}
      {(hasPrev || hasNext) && (
        <div className="kwic-pager">
          <button
            type="button"
            disabled={!hasPrev || disabled}
            onClick={() => void workspace.runSearchAt(Math.max(0, response.offset - PAGE_SIZE))}
          >
            ← Prev
          </button>
          <button
            type="button"
            disabled={!hasNext || disabled}
            onClick={() => void workspace.runSearchAt(response.offset + PAGE_SIZE)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function refineQuery(extension: string) {
  workspace.setSearchQuery(extension);
  void workspace.runSearch();
}

function OverviewPanel({
  query,
  total,
  overview,
  disabled,
}: {
  query: string;
  total: number;
  overview: SearchOverview;
  disabled: boolean;
}) {
  const totalLabel = overview.approximate ? `~${total}` : `${total}`;
  const hasLeft = overview.trigram_left.length > 0;
  const hasRight = overview.trigram_right.length > 0;
  return (
    <div className="kwic-overview">
      <div className="kwic-overview-banner">
        <strong>Too many results</strong> — {totalLabel} hits for “{query}”.
        Pick a context extension below (or use the facets) to bring the
        result set under the {overview.threshold} threshold.
      </div>
      {overview.kwic_filters_ignored ? (
        <div className="kwic-overview-note">
          KWIC-based filters (left/right char, bigram, binom) are ignored
          while the result set exceeds {overview.threshold} — narrow first,
          then re-apply them.
        </div>
      ) : null}
      {(hasLeft || hasRight) ? (
        <div className="kwic-overview-extensions">
          {hasLeft ? (
            <div className="kwic-overview-col">
              <div className="kwic-overview-col-title">Left context</div>
              {overview.trigram_left.map((t) => (
                <button
                  key={`l:${t.gram}`}
                  type="button"
                  className="kwic-overview-ext"
                  disabled={disabled}
                  onClick={() => refineQuery(t.gram)}
                  title={`Refine to ${t.gram} (${t.count} hits)`}
                >
                  <span className="kwic-overview-ext-gram">{t.gram}</span>
                  <span className="kwic-overview-ext-count">{t.count}</span>
                </button>
              ))}
            </div>
          ) : null}
          {hasRight ? (
            <div className="kwic-overview-col">
              <div className="kwic-overview-col-title">Right context</div>
              {overview.trigram_right.map((t) => (
                <button
                  key={`r:${t.gram}`}
                  type="button"
                  className="kwic-overview-ext"
                  disabled={disabled}
                  onClick={() => refineQuery(t.gram)}
                  title={`Refine to ${t.gram} (${t.count} hits)`}
                >
                  <span className="kwic-overview-ext-gram">{t.gram}</span>
                  <span className="kwic-overview-ext-count">{t.count}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function SearchTab() {
  const [expandedFacets, setExpandedFacets] = useState<Set<FacetGroupKey>>(() => new Set());
  const status = useWorkspace((s) => s.search.status);
  const error = useWorkspace((s) => s.search.error);
  const response = useWorkspace((s) => s.search.response);
  const translationResponse = useWorkspace((s) => s.search.translationResponse);
  const translationFilters = useWorkspace((s) => s.search.translationFilters);
  const parallelResponse = useWorkspace((s) => s.search.parallelResponse);
  const target = useWorkspace((s) => s.search.target);
  const query = useWorkspace((s) => s.search.query);
  const filters = useWorkspace((s) => s.search.filters);
  const facetLimit = useWorkspace((s) => s.search.facetLimit);
  const masterOnly = useWorkspace((s) => s.searchPrefs.masterOnly);
  const textLists = useWorkspace((s) => s.textLists);
  const activeListPaths = useWorkspace((s) => s.activeListPaths);
  const listFilterMode = useWorkspace((s) => s.listFilterMode);
  const activePathSet = new Set(activeListPaths);
  const activeLists = textLists.filter((list) => activePathSet.has(list.path));
  const facetHasMore = (values: SearchFacetValue[]) =>
    facetLimit <= DEFAULT_FACET_LIMIT
      ? values.length >= DEFAULT_FACET_LIMIT
      : values.length > DEFAULT_FACET_LIMIT;
  useEffect(() => {
    if (facetLimit <= DEFAULT_FACET_LIMIT) {
      setExpandedFacets(new Set());
    }
  }, [facetLimit]);
  const expandFacet = (key: FacetGroupKey) => {
    setExpandedFacets((cur) => {
      if (cur.has(key)) return cur;
      const next = new Set(cur);
      next.add(key);
      return next;
    });
    if (facetLimit < EXPANDED_FACET_LIMIT) {
      void workspace.setSearchFacetLimit(EXPANDED_FACET_LIMIT);
    }
  };

  if (status === "idle") {
    return <div className="rc empty">Enter a query in the menu bar to search.</div>;
  }
  if (status === "loading" && response == null && translationResponse == null) {
    return <div className="rc empty">Searching…</div>;
  }
  if (status === "error") {
    return <div className="rc empty">Search failed: {error}</div>;
  }

  if (target === "translations") {
    if (translationResponse == null) {
      return <div className="rc empty">No results.</div>;
    }
    if (translationResponse.total === 0) {
      return <div className="rc empty">No matches for "{query}".</div>;
    }
    return (
      <TranslationResultsView
        response={translationResponse}
        query={query}
        filters={translationFilters}
        status={status}
      />
    );
  }

  if (target === "parallel") {
    if (parallelResponse == null) {
      return <div className="rc empty">No results.</div>;
    }
    if (parallelResponse.total === 0) {
      return <div className="rc empty">No clusters for "{query}".</div>;
    }
    return <ParallelResultsView response={parallelResponse} query={query} status={status} />;
  }

  if (response == null) {
    return <div className="rc empty">No results.</div>;
  }
  if (response.total === 0) {
    const filtered = hasAnyFilter(filters);
    return (
      <div className="rc empty">
        <div>No matches for “{query}”.</div>
        {filtered ? (
          <button
            type="button"
            className="kwic-empty-reset"
            disabled={status === "loading"}
            onClick={() => void workspace.clearSearchFilters()}
          >
            Reset facets
          </button>
        ) : null}
      </div>
    );
  }

  const isOverview = response.overview != null;
  const start = response.offset + 1;
  const end = Math.min(response.offset + response.limit, response.total);
  const hasPrev = !isOverview && response.offset > 0;
  const hasNext = !isOverview && response.offset + response.limit < response.total;
  const facets = response.facets ? normalizeFacets(response.facets) : EMPTY_FACETS;
  const disabled = status === "loading";
  const filtered = hasAnyFilter(filters);
  const saveSearchList = () => {
    const name = window.prompt("Save matched texts as list", `Search ${response.query}`);
    if (!name) return;
    void workspace.saveSearchAsTextList(listPathFromName(name));
  };

  const goPage = async (nextOffset: number) => {
    // Bounded reuse: re-run the search with a new offset. Cheapest path —
    // /search is server-side paginated and the index reads are fast.
    const { runSearchAt } = workspace;
    await runSearchAt(nextOffset);
  };

  return (
    <div className="rc kwic-list">
      <div className="kwic-summary">
        <span>
          {isOverview
            ? `${response.overview!.approximate ? "~" : ""}${response.total} for “${response.query}” (overview)`
            : `${start}–${end} of ${response.total} for “${response.query}”`}
        </span>
        <span className="kwic-sort">· {response.sort}</span>
        {listFilterMode !== "off" ? (
          <span className="kwic-sort">· lists: {listFilterMode}</span>
        ) : null}
        <button
          type="button"
          className="kwic-clear-filters"
          disabled={disabled}
          onClick={saveSearchList}
        >
          save list
        </button>
        {filtered ? (
          <button
            type="button"
            className="kwic-clear-filters"
            disabled={disabled}
            onClick={() => void workspace.clearSearchFilters()}
          >
            clear facets
          </button>
        ) : null}
      </div>
      {isOverview ? (
        <OverviewPanel
          query={response.query}
          total={response.total}
          overview={response.overview!}
          disabled={disabled}
        />
      ) : null}
      <div className="kwic-facets">
        <ListFacetGroup
          lists={activeLists}
          mode={listFilterMode}
          disabled={disabled}
        />
        <TextFacetGroup
          values={facets.textid}
          selected={filters.textid}
          disabled={disabled}
          expanded={expandedFacets.has("textid")}
          hasMore={facetHasMore(facets.textid)}
          onMore={() => expandFacet("textid")}
        />
        <FacetGroup
          label="Category"
          values={facets.category}
          kind="category"
          disabled={disabled}
          expanded={expandedFacets.has("category")}
          hasMore={facetHasMore(facets.category)}
          onMore={() => expandFacet("category")}
        />
        <DateFacet
          before={filters.dateBefore}
          after={filters.dateAfter}
          currentTextid={facets.date.current_textid}
          currentDate={facets.date.current_text_date}
          min={facets.date.min}
          max={facets.date.max}
          disabled={disabled}
        />
        <FacetGroup
          label="Witness"
          values={facets.witness}
          kind="witness"
          disabled={disabled}
          expanded={expandedFacets.has("witness")}
          hasMore={facetHasMore(facets.witness)}
          onMore={() => expandFacet("witness")}
          masterOnly={masterOnly}
        />
        <FacetGroup
          label="Voice"
          values={facets.voice}
          kind="voice"
          disabled={disabled}
          expanded={expandedFacets.has("voice")}
          hasMore={facetHasMore(facets.voice)}
          onMore={() => expandFacet("voice")}
        />
        <FacetGroup
          label="Left"
          values={facets.left_char}
          kind="leftChar"
          disabled={disabled}
          expanded={expandedFacets.has("leftChar")}
          hasMore={facetHasMore(facets.left_char)}
          onMore={() => expandFacet("leftChar")}
        />
        <FacetGroup
          label="Right"
          values={facets.right_char}
          kind="rightChar"
          disabled={disabled}
          expanded={expandedFacets.has("rightChar")}
          hasMore={facetHasMore(facets.right_char)}
          onMore={() => expandFacet("rightChar")}
        />
        <FacetGroup
          label="Left 2"
          values={facets.left_bigram}
          kind="leftBigram"
          disabled={disabled}
          expanded={expandedFacets.has("leftBigram")}
          hasMore={facetHasMore(facets.left_bigram)}
          onMore={() => expandFacet("leftBigram")}
        />
        <FacetGroup
          label="Right 2"
          values={facets.right_bigram}
          kind="rightBigram"
          disabled={disabled}
          expanded={expandedFacets.has("rightBigram")}
          hasMore={facetHasMore(facets.right_bigram)}
          onMore={() => expandFacet("rightBigram")}
        />
        <FacetGroup
          label="Binom"
          values={facets.around_binom}
          kind="aroundBinom"
          disabled={disabled}
          expanded={expandedFacets.has("aroundBinom")}
          hasMore={facetHasMore(facets.around_binom)}
          onMore={() => expandFacet("aroundBinom")}
        />
      </div>
      {isOverview
        ? null
        : response.hits.map((h, i) => (
            <HitRow key={`${h.textid}:${h.juan_seq}:${h.master_offset}:${i}`} hit={h} />
          ))}
      {(hasPrev || hasNext) && (
        <div className="kwic-pager">
          <button
            type="button"
            disabled={!hasPrev || status === "loading"}
            onClick={() => goPage(Math.max(0, response.offset - PAGE_SIZE))}
          >
            ← Prev
          </button>
          <button
            type="button"
            disabled={!hasNext || status === "loading"}
            onClick={() => goPage(response.offset + PAGE_SIZE)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
