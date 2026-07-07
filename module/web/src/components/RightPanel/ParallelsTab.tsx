import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  generateJuanParallels,
  getJuanParallels,
  getJuanParallelsStatus,
  startGithubLogin,
} from "../../api/client";
import type {
  DiffOp,
  JuanParallelLocation,
  JuanParallelsGenerationParams,
  JuanParallelsResponse,
  JuanParallelRemoteText,
} from "../../api/types";
import { krClass } from "../../lib/krClass";
import { useWorkspace, workspace } from "../../state/useWorkspace";

const PAGE_SIZE = 50;
const DEFAULT_GENERATION_PARAMS: JuanParallelsGenerationParams = {
  bucket: "all",
  minLength: 12,
  maxLength: null,
  minOccurrences: 2,
  maxPostings: 500,
  maxEdits: 4,
  context: 20,
  includeContained: false,
};

function parallelBucket(
  value: string | undefined,
): "front" | "body" | "back" | undefined {
  return value === "front" || value === "body" || value === "back" ? value : undefined;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function renderDiffText(
  text: string,
  diff: DiffOp[] | undefined,
  side: "local" | "remote",
): ReactNode[] {
  if (!diff || diff.length === 0) return [<span key="plain">{text}</span>];
  const nodes: ReactNode[] = [];
  let i = 0;
  for (const op of diff) {
    if (op[0] === "=") {
      const n = op[1];
      if (n > 0) {
        nodes.push(<span key={`eq:${i}`}>{text.slice(i, i + n)}</span>);
        i += n;
      }
      continue;
    }
    if (op[0] === "s") {
      nodes.push(
        <mark key={`sub:${i}`} className="diff-sub" title={side === "local" ? `→ ${op[2]}` : `→ ${op[1]}`}>
          {text[i] ?? "□"}
        </mark>,
      );
      i += 1;
      continue;
    }
    if (op[0] === "d") {
      if (side === "local") {
        nodes.push(
          <mark key={`del:${i}`} className="diff-del" title="present only in the local passage">
            {text[i] ?? op[1] ?? "□"}
          </mark>,
        );
        i += 1;
      } else {
        nodes.push(
          <mark key={`gap:${i}`} className="diff-gap" title="missing in the remote passage">
            □
          </mark>,
        );
      }
      continue;
    }
    if (side === "local") {
      nodes.push(
        <mark key={`gap:${i}`} className="diff-gap" title="missing in the local passage">
          □
        </mark>,
      );
    } else {
      nodes.push(
        <mark key={`ins:${i}:${nodes.length}`} className="diff-ins" title={`inserted: ${op[1]}`}>
          {text[i] ?? op[1] ?? "□"}
        </mark>,
      );
      i += 1;
    }
  }
  if (i < text.length) nodes.push(<span key="tail">{text.slice(i)}</span>);
  return nodes;
}

function ParallelCard({
  location,
  sourceTextid,
  sourceSeq,
  showRemoteGap,
}: {
  location: JuanParallelLocation;
  sourceTextid: string;
  sourceSeq: number;
  showRemoteGap: boolean;
}) {
  const highlightLocal = () => {
    workspace.openTextLocation({
      textid: sourceTextid,
      seq: sourceSeq,
      bucket: location.local_bucket,
      offset: location.local_offset,
      length: location.local_length,
    });
  };
  const openRemote = () => {
    if (!location.available) return;
    workspace.openTextLocation({
      textid: location.textid,
      seq: location.juan_seq,
      bucket: location.bucket,
      offset: location.offset,
      length: location.length,
    });
  };
  return (
    <div className="parallel-row">
      <button
        type="button"
        className="kwic-row parallel-main"
        onClick={highlightLocal}
        title={`Open local ${location.local_bucket} @${location.local_offset}`}
      >
        <div className="kwic-meta">
          {location.toc_label || location.title ? (
            <span className="kwic-label">{location.toc_label ?? location.title}</span>
          ) : null}
          <span className={`kwic-textid ${krClass(location.textid)}`}>{location.textid}</span>
          <span className="kwic-juan">juan {location.juan_seq}</span>
          {location.bucket !== "body" ? (
            <span className="kwic-chip">{location.bucket}</span>
          ) : null}
          {location.edit_distance > 0 ? (
            <span className="kwic-chip" title="Edit distance">Δ{location.edit_distance}</span>
          ) : null}
        </div>
        {location.available ? (
          <div className="parallel-diff">
            <div className="parallel-diff-row">
              <div className="parallel-diff-label">Local</div>
              <div className="parallel-diff-text">
                {renderDiffText(location.local_text ?? "", location.diff, "local")}
              </div>
            </div>
            <div className="parallel-diff-row">
              <div className="parallel-diff-label">Remote</div>
              <div className="parallel-diff-text">
                {renderDiffText(location.text, location.diff, "remote")}
              </div>
            </div>
          </div>
        ) : (
          <div className="parallel-unavailable">Remote passage unavailable</div>
        )}
        {(location.local_gap != null || location.remote_gap != null) && (
          <div className="parallel-gaps">
            {location.local_gap != null ? (
              <span className="parallel-gap">local gap {location.local_gap}</span>
            ) : null}
            {showRemoteGap && location.remote_gap != null ? (
              <span className="parallel-gap">remote gap {location.remote_gap}</span>
            ) : null}
          </div>
        )}
        <div className="parallel-remote">
          remote {location.textid} · juan {location.juan_seq}
          {" · "}
          {location.bucket} @{location.offset}+{location.length}
        </div>
        <div className="parallel-local">
          local {location.local_bucket} @{location.local_offset}+{location.local_length}
        </div>
      </button>
      <button
        type="button"
        className="parallel-open"
        disabled={!location.available}
        onClick={openRemote}
        title={
          location.available
            ? `Open remote ${location.textid} · juan ${location.juan_seq}`
            : "Remote passage unavailable"
        }
        aria-label="Open remote passage"
      >
        ↗
      </button>
    </div>
  );
}

interface ParallelGroup {
  textid: string;
  title: string | null;
  count: number;
  overlap_length: number;
  locations: JuanParallelLocation[];
}

function groupParallelLocations(
  locations: JuanParallelLocation[],
  remoteTexts: JuanParallelRemoteText[],
): ParallelGroup[] {
  const byTextid = new Map<string, JuanParallelLocation[]>();
  for (const location of locations) {
    const group = byTextid.get(location.textid);
    if (group) group.push(location);
    else byTextid.set(location.textid, [location]);
  }
  return remoteTexts
    .filter((item) => byTextid.has(item.textid))
    .map((item) => ({
      textid: item.textid,
      title: item.title,
      count: item.count,
      overlap_length: item.overlap_length,
      locations: byTextid.get(item.textid) ?? [],
    }));
}

export function ParallelsTab() {
  const activeTextid = useWorkspace((s) => s.activeTextid);
  const activeSeq = useWorkspace((s) => s.activeSeq);
  const source = useWorkspace((s) => s.parallelsSource);
  const selection = useWorkspace((s) => s.selection);
  const authStatus = useWorkspace((s) => s.auth.status);
  const [offset, setOffset] = useState(0);
  const [response, setResponse] = useState<JuanParallelsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lengthFilter, setLengthFilter] = useState<{ min: number; max: number } | null>(null);
  const [sortMode, setSortMode] = useState<"local" | "remote">("local");
  const [remoteTextid, setRemoteTextid] = useState<string | null>(null);
  const [assetState, setAssetState] = useState<
    "checking" | "confirming" | "generating" | "ready"
  >("checking");
  const [notice, setNotice] = useState<string | null>(null);
  const [generationParams, setGenerationParams] = useState<JuanParallelsGenerationParams>(
    DEFAULT_GENERATION_PARAMS,
  );

  const textid = source?.textid ?? activeTextid;
  const seq = source?.seq ?? activeSeq;
  const activeSelection =
    selection != null && selection.textid === textid && selection.seq === seq
      ? selection
      : null;

  useEffect(() => {
    setOffset(0);
  }, [
    activeSelection?.bucket,
    activeSelection?.start,
    activeSelection?.end,
  ]);

  useEffect(() => {
    setOffset(0);
    setLengthFilter(null);
    setSortMode("local");
    setRemoteTextid(null);
    setAssetState("checking");
    setNotice(null);
    setGenerationParams({ ...DEFAULT_GENERATION_PARAMS });
  }, [textid, seq]);

  useEffect(() => {
    if (textid == null || seq == null) {
      setResponse(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setResponse(null);
    setError(null);
    setAssetState("checking");
    getJuanParallelsStatus(textid, seq)
      .then((status) => {
        if (cancelled) return;
        if (status.has_assets) {
          setAssetState("ready");
          return;
        }
        if (!status.can_generate) {
          throw new Error("No stored parallels were found and the corpus index is unavailable.");
        }
        setAssetState("confirming");
      })
      .catch((reason) => {
        if (!cancelled) setError(String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [textid, seq]);

  useEffect(() => {
    if (textid == null || seq == null || assetState !== "ready") return;
    let cancelled = false;
    setResponse(null);
    setError(null);
    getJuanParallels(textid, seq, {
      offset,
      limit: PAGE_SIZE,
      bucket: parallelBucket(activeSelection?.bucket),
      start: activeSelection?.start,
      end: activeSelection?.end,
      minLength: lengthFilter?.min,
      maxLength: lengthFilter?.max,
      sort: sortMode,
      remoteTextid,
    })
      .then((value) => {
        if (!cancelled) setResponse(value);
      })
      .catch((reason) => {
        if (!cancelled) setError(String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [
    textid,
    seq,
    assetState,
    offset,
    activeSelection?.bucket,
    activeSelection?.start,
    activeSelection?.end,
    lengthFilter?.min,
    lengthFilter?.max,
    sortMode,
    remoteTextid,
  ]);

  const runGeneration = async (event: FormEvent) => {
    event.preventDefault();
    if (textid == null || seq == null) return;
    setError(null);
    setAssetState("generating");
    setNotice("Finding parallels with the confirmed parameters…");
    try {
      const result = await generateJuanParallels(textid, seq, generationParams);
      setNotice(result.message);
      setAssetState("ready");
      window.dispatchEvent(new CustomEvent("bkk:juan-parallels-changed", {
        detail: { textid, seq, hasParallels: result.has_parallels },
      }));
    } catch (reason) {
      setError(String(reason));
      setAssetState("confirming");
    }
  };

  if (textid == null || seq == null) {
    return <div className="rc empty">Open a juan to see parallel passages.</div>;
  }
  if (assetState === "confirming") {
    if (authStatus === "loading" || authStatus === "unknown") {
      return (
        <div className="rc parallel-panel">
          <div className="parallel-gate">
            <div className="parallel-kicker">Find parallels</div>
            <h3>Checking login status…</h3>
            <p>Waiting for the current session before offering an on-demand scan.</p>
          </div>
        </div>
      );
    }
    if (authStatus !== "authenticated") {
      return (
        <div className="rc parallel-panel">
          <div className="parallel-gate">
            <div className="parallel-kicker">Find parallels</div>
            <h3>Login required</h3>
            <p>
              Log in with GitHub before starting an on-demand parallel scan for {textid}, 卷 {seq}.
            </p>
            <div className="parallel-gate-actions">
              <button type="button" className="parallel-gate-login" onClick={startGithubLogin}>
                GitHub Login
              </button>
              <button type="button" onClick={() => workspace.setRightTab("annotations")}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      );
    }
    return (
      <div className="rc parallel-panel">
        <form className="parallel-generate" onSubmit={runGeneration}>
          <div className="parallel-kicker">Find parallels</div>
          <h3>Confirm scan parameters</h3>
          <p>
            No parallel assets exist for {textid}, 卷 {seq}. Review these settings
            before starting the corpus scan.
          </p>
          {error != null && <div className="parallel-generate-error">{error}</div>}
          <div className="parallel-generate-grid">
            <label>
              Bucket (--bucket)
              <select
                value={generationParams.bucket}
                onChange={(event) => setGenerationParams({
                  ...generationParams,
                  bucket: event.target.value as JuanParallelsGenerationParams["bucket"],
                })}
              >
                <option value="all">all</option>
                <option value="body">body</option>
                <option value="front">front</option>
                <option value="back">back</option>
              </select>
            </label>
            <label>
              Minimum length (--min-length)
              <input
                type="number"
                min={3}
                required
                value={generationParams.minLength}
                onChange={(event) => setGenerationParams({
                  ...generationParams,
                  minLength: Number(event.target.value),
                })}
              />
            </label>
            <label>
              Maximum result length (post-filter)
              <input
                type="number"
                min={generationParams.minLength}
                placeholder="no limit"
                value={generationParams.maxLength ?? ""}
                onChange={(event) => setGenerationParams({
                  ...generationParams,
                  maxLength: event.target.value === "" ? null : Number(event.target.value),
                })}
              />
            </label>
            <label>
              Edit distance (--max-edits)
              <input
                type="number"
                min={0}
                max={4}
                required
                value={generationParams.maxEdits}
                onChange={(event) => setGenerationParams({
                  ...generationParams,
                  maxEdits: Number(event.target.value),
                })}
              />
            </label>
            <label>
              Minimum occurrences (--min-occurrences)
              <input
                type="number"
                min={2}
                required
                value={generationParams.minOccurrences}
                onChange={(event) => setGenerationParams({
                  ...generationParams,
                  minOccurrences: Number(event.target.value),
                })}
              />
            </label>
            <label>
              Maximum postings (--max-postings)
              <input
                type="number"
                min={2}
                required
                value={generationParams.maxPostings}
                onChange={(event) => setGenerationParams({
                  ...generationParams,
                  maxPostings: Number(event.target.value),
                })}
              />
            </label>
            <label>
              Context characters (--context)
              <input
                type="number"
                min={0}
                max={500}
                required
                value={generationParams.context}
                onChange={(event) => setGenerationParams({
                  ...generationParams,
                  context: Number(event.target.value),
                })}
              />
            </label>
          </div>
          <label className="parallel-generate-check">
            <input
              type="checkbox"
              checked={generationParams.includeContained}
              onChange={(event) => setGenerationParams({
                ...generationParams,
                includeContained: event.target.checked,
              })}
            />
            Include passages wholly contained in longer matches (--include-contained)
          </label>
          <div className="parallel-generate-note">
            Maximum result length is applied after discovery; the other settings map
            directly to the parallel scanner.
          </div>
          <div className="parallel-generate-actions">
            <button type="button" onClick={() => workspace.setRightTab("annotations")}>
              Cancel
            </button>
            <button type="submit">Run scan</button>
          </div>
        </form>
      </div>
    );
  }
  if (error != null) {
    return <div className="rc empty">Failed to load parallels: {error}</div>;
  }
  if (assetState === "generating") {
    return (
      <div className="rc parallel-panel">
        <div className="parallel-notice">{notice}</div>
      </div>
    );
  }
  if (response == null || response.textid !== textid || response.juan_seq !== seq) {
    return (
      <div className="rc empty">
        {assetState === "checking"
          ? "Checking for stored parallel passages…"
          : "Loading parallel passages…"}
      </div>
    );
  }

  const pageStart = response.total === 0 ? 0 : response.offset + 1;
  const pageEnd = Math.min(response.total, response.offset + response.locations.length);
  const hasPrev = response.offset > 0;
  const hasNext = response.offset + response.locations.length < response.total;
  const availableMinLength = response.available_min_length;
  const availableMaxLength = response.available_max_length;
  const currentMin = lengthFilter?.min ?? availableMinLength;
  const currentMax = lengthFilter?.max ?? availableMaxLength;
  const canFilter = availableMaxLength > availableMinLength;
  const sourceTitle = response.source_title ?? textid;
  const remoteTextOptions = response.remote_texts;
  const remoteTextLabel = (item: JuanParallelRemoteText) =>
    item.title ? `${item.title} · ${item.textid}` : item.textid;
  const remoteRows = sortMode === "remote";
  const remoteGroups = remoteRows
    ? groupParallelLocations(response.locations, remoteTextOptions)
    : [];

  return (
    <div className="rc parallel-panel">
      {notice != null && <div className="parallel-notice">{notice}</div>}
      <div className="parallel-header">
        <div className="parallel-title">
          <div className="parallel-kicker">Parallels</div>
          <div className="parallel-source-title">{sourceTitle}</div>
          <div className="parallel-source-meta">
            <span>{textid}</span>
            <span>卷 {seq}</span>
            <span>{response.source_char_count} chars</span>
          </div>
        </div>
        <button
          type="button"
          className="parallel-rebind"
          onClick={() => workspace.openParallelsPanel(textid, seq)}
          title="Bind the parallels tab to this juan"
        >
          Load
        </button>
      </div>
      <div className="parallel-order">
        <div className="parallel-order-row">
          <span>Sort</span>
          <div className="parallel-order-buttons">
            <button
              type="button"
              className={`kwic-facet-chip${sortMode === "local" ? " on" : ""}`}
              onClick={() => {
                if (sortMode === "local") return;
                setSortMode("local");
                setOffset(0);
              }}
            >
              local
            </button>
            <button
              type="button"
              className={`kwic-facet-chip${sortMode === "remote" ? " on" : ""}`}
              onClick={() => {
                if (sortMode === "remote") return;
                setSortMode("remote");
                setOffset(0);
              }}
            >
              remote
            </button>
          </div>
        </div>
        <label className="parallel-order-row">
          <span>Remote text</span>
          <select
            value={remoteTextid ?? ""}
            onChange={(event) => {
              const next = event.target.value || null;
              setRemoteTextid(next);
              setOffset(0);
            }}
          >
            <option value="">All remote texts</option>
            {remoteTextOptions.map((item) => (
              <option key={item.textid} value={item.textid}>
                {remoteTextLabel(item)} ({item.count})
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="parallel-limits">
        <div className="parallel-limits-meta">
          <span>Length filter</span>
          <span>
            {currentMin}–{currentMax}
            {canFilter ? ` of ${availableMinLength}–${availableMaxLength}` : ""}
          </span>
        </div>
        <div className="parallel-sliders">
          <input
            type="range"
            min={availableMinLength}
            max={availableMaxLength}
            step={1}
            value={currentMin}
            disabled={!canFilter}
            onChange={(event) => {
              if (!canFilter) return;
              const nextMin = clamp(Number(event.target.value), availableMinLength, currentMax);
              setLengthFilter({ min: nextMin, max: Math.max(nextMin, currentMax) });
              setOffset(0);
            }}
          />
          <input
            type="range"
            min={availableMinLength}
            max={availableMaxLength}
            step={1}
            value={currentMax}
            disabled={!canFilter}
            onChange={(event) => {
              if (!canFilter) return;
              const nextMax = clamp(Number(event.target.value), currentMin, availableMaxLength);
              setLengthFilter({ min: Math.min(currentMin, nextMax), max: nextMax });
              setOffset(0);
            }}
          />
        </div>
        <div className="parallel-limits-fields">
          <label>
            min
            <input
              type="number"
              min={availableMinLength}
              max={currentMax}
              step={1}
              value={currentMin}
              disabled={!canFilter}
              onChange={(event) => {
                if (!canFilter) return;
                const parsed = Number(event.target.value);
                if (Number.isNaN(parsed)) return;
                const nextMin = clamp(parsed, availableMinLength, currentMax);
                setLengthFilter({ min: nextMin, max: Math.max(nextMin, currentMax) });
                setOffset(0);
              }}
            />
          </label>
          <label>
            max
            <input
              type="number"
              min={currentMin}
              max={availableMaxLength}
              step={1}
              value={currentMax}
              disabled={!canFilter}
              onChange={(event) => {
                if (!canFilter) return;
                const parsed = Number(event.target.value);
                if (Number.isNaN(parsed)) return;
                const nextMax = clamp(parsed, currentMin, availableMaxLength);
                setLengthFilter({ min: Math.min(currentMin, nextMax), max: nextMax });
                setOffset(0);
              }}
            />
          </label>
          <button
            type="button"
            onClick={() => {
              setLengthFilter(null);
              setOffset(0);
            }}
            disabled={lengthFilter == null}
          >
            Reset
          </button>
        </div>
      </div>
      {activeSelection != null && (
        <div className="parallel-filter">
          <div>
            Overlapping “{activeSelection.chars.join("")}”
            <span>{response.total} result{response.total === 1 ? "" : "s"}</span>
          </div>
          <button type="button" onClick={() => workspace.setSelection(null)}>clear</button>
        </div>
      )}
      {response.total === 0 ? (
        <div className="empty">
          {activeSelection == null
            ? (lengthFilter == null
                ? "No parallel passages are available for this juan."
                : "No parallel passages match the current length filter.")
            : "No parallel passages overlap the selection."}
        </div>
      ) : (
        <>
          <div className="parallel-count">{pageStart}–{pageEnd} of {response.total}</div>
          {remoteRows ? (
            remoteGroups.map((group) => (
              <section className="parallel-group" key={group.textid}>
                <div className="parallel-group-head">
                  <div className="parallel-group-title">
                    <span className="parallel-group-name">
                      {group.title ? group.title : group.textid}
                    </span>
                    <span className="parallel-group-textid">{group.textid}</span>
                  </div>
                  <div className="parallel-group-meta">
                    <span>{group.count} passage{group.count === 1 ? "" : "s"}</span>
                    <span>overlap {group.overlap_length}</span>
                  </div>
                </div>
                <div className="parallel-group-body">
                  {group.locations.map((location) => (
                    <ParallelCard
                      key={location.id}
                      location={location}
                      sourceTextid={textid}
                      sourceSeq={seq}
                      showRemoteGap={remoteRows}
                    />
                  ))}
                </div>
              </section>
            ))
          ) : (
            response.locations.map((location) => (
              <ParallelCard
                key={location.id}
                location={location}
                sourceTextid={textid}
                sourceSeq={seq}
                showRemoteGap={remoteRows}
              />
            ))
          )}
          {(hasPrev || hasNext) && (
            <div className="kwic-pager">
              <button
                type="button"
                disabled={!hasPrev}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                ← Prev
              </button>
              <button
                type="button"
                disabled={!hasNext}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
