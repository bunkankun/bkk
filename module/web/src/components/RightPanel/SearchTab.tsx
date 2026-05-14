import type { SearchHit } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";

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

function HitRow({ hit }: { hit: SearchHit }) {
  const witness = hit.matched_via !== "master" ? hit.matched_via : null;
  const left = trimLeftContext(hit.left);
  const right = trimRightContext(hit.right);
  const leftElided = left.length < hit.left.length;
  const rightElided = right.length < hit.right.length;
  const wLeftRaw = hit.witness_left ?? "";
  const wRightRaw = hit.witness_right ?? "";
  const showWitnessLine = witness !== null && (wLeftRaw.length > 0 || wRightRaw.length > 0);
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
      title={`${hit.textid} · juan ${hit.juan_seq} · @${hit.master_offset}`}
    >
      <div className="kwic-meta">
        {hit.toc_label ? <span className="kwic-label">{hit.toc_label}</span> : null}
        <span className="kwic-textid">{hit.textid}</span>
        <span className="kwic-juan">juan {hit.juan_seq}</span>
        {witness ? <span className="kwic-chip">{witness}</span> : null}
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
    return <div className="rc empty">No matches for “{query}”.</div>;
  }

  const start = response.offset + 1;
  const end = Math.min(response.offset + response.limit, response.total);
  const hasPrev = response.offset > 0;
  const hasNext = response.offset + response.limit < response.total;

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
