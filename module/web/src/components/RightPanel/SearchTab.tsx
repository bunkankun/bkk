import type { SearchFacetValue, SearchFacets, SearchHit } from "../../api/types";
import { listPathFromName } from "../../lib/textLists";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import type { SearchFacetKind, SearchFilters } from "../../state/useWorkspace";

const PAGE_SIZE = 50;

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
      filters.category.length ||
      filters.dateBefore != null ||
      filters.dateAfter != null ||
      filters.witness.length ||
      filters.voice.length ||
      filters.leftChar.length ||
      filters.rightChar.length ||
      filters.leftBigram.length ||
      filters.rightBigram.length ||
      filters.aroundBinom.length,
  );
}

function FacetGroup({
  label,
  values,
  kind,
  disabled,
}: {
  label: string;
  values: SearchFacetValue[];
  kind: SearchFacetKind;
  disabled: boolean;
}) {
  if (values.length === 0) return null;
  return (
    <div className="kwic-facet-group">
      <div className="kwic-facet-label">{label}</div>
      <div className="kwic-facet-values">
        {values.map((v) => (
          <button
            key={v.value}
            type="button"
            className={`kwic-facet-chip${v.selected ? " on" : ""}`}
            disabled={disabled}
            onClick={() => void workspace.toggleSearchFacet(kind, v.value)}
            title={v.label ?? v.value}
          >
            <span className="kwic-facet-value">{v.value}</span>
            <span className="kwic-facet-count">{v.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function TextFacetGroup({
  values,
  selected,
  disabled,
}: {
  values: SearchFacetValue[];
  selected: string | null;
  disabled: boolean;
}) {
  if (values.length === 0) return null;
  return (
    <div className="kwic-facet-group">
      <div className="kwic-facet-label">Text</div>
      <div className="kwic-facet-values">
        {values.map((v) => (
          <button
            key={v.value}
            type="button"
            className={`kwic-facet-chip${v.value === selected ? " on" : ""}`}
            disabled={disabled}
            onClick={() => void workspace.setSearchTextid(v.value === selected ? null : v.value)}
            title={v.label ? `${v.value} · ${v.label}` : v.value}
          >
            <span className="kwic-facet-value">{v.value}</span>
            <span className="kwic-facet-count">{v.count}</span>
          </button>
        ))}
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
  const setYear = (which: "before" | "after", raw: string) => {
    const trimmed = raw.trim();
    const value = trimmed === "" ? null : Number(trimmed);
    if (value == null || Number.isFinite(value)) {
      void workspace.setSearchDateFilter(which, value);
    }
  };
  return (
    <div className="kwic-facet-group">
      <div className="kwic-facet-label">Date</div>
      <div className="kwic-date-row">
        <label>
          <span>&lt;</span>
          <input
            type="number"
            value={before ?? ""}
            disabled={disabled}
            placeholder={max != null ? String(max) : "year"}
            onChange={(e) => setYear("before", e.target.value)}
          />
        </label>
        <label>
          <span>&gt;</span>
          <input
            type="number"
            value={after ?? ""}
            disabled={disabled}
            placeholder={min != null ? String(min) : "year"}
            onChange={(e) => setYear("after", e.target.value)}
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
        <span className="kwic-textid">{hit.textid}</span>
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

export function SearchTab() {
  const status = useWorkspace((s) => s.search.status);
  const error = useWorkspace((s) => s.search.error);
  const response = useWorkspace((s) => s.search.response);
  const query = useWorkspace((s) => s.search.query);
  const filters = useWorkspace((s) => s.search.filters);
  useWorkspace((s) => s.activeListPaths);
  const listFilterMode = useWorkspace((s) => s.listFilterMode);

  if (status === "idle") {
    return <div className="rc empty">Enter a query in the menu bar to search.</div>;
  }
  if (status === "loading" && response == null) {
    return <div className="rc empty">Searching…</div>;
  }
  if (status === "error") {
    return <div className="rc empty">Search failed: {error}</div>;
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

  const start = response.offset + 1;
  const end = Math.min(response.offset + response.limit, response.total);
  const hasPrev = response.offset > 0;
  const hasNext = response.offset + response.limit < response.total;
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
          {start}–{end} of {response.total} for “{response.query}”
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
      <div className="kwic-facets">
        <TextFacetGroup
          values={facets.textid}
          selected={filters.textid}
          disabled={disabled}
        />
        <FacetGroup
          label="Category"
          values={facets.category}
          kind="category"
          disabled={disabled}
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
        />
        <FacetGroup
          label="Voice"
          values={facets.voice}
          kind="voice"
          disabled={disabled}
        />
        <FacetGroup
          label="Left"
          values={facets.left_char}
          kind="leftChar"
          disabled={disabled}
        />
        <FacetGroup
          label="Right"
          values={facets.right_char}
          kind="rightChar"
          disabled={disabled}
        />
        <FacetGroup
          label="Left 2"
          values={facets.left_bigram}
          kind="leftBigram"
          disabled={disabled}
        />
        <FacetGroup
          label="Right 2"
          values={facets.right_bigram}
          kind="rightBigram"
          disabled={disabled}
        />
        <FacetGroup
          label="Binom"
          values={facets.around_binom}
          kind="aroundBinom"
          disabled={disabled}
        />
      </div>
      {response.hits.map((h, i) => (
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
