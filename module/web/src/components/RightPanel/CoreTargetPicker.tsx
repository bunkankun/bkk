import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  getAnnotationSenseCounts,
  getCoreSuperEntryByOrthFull,
  postAnnotation,
} from "../../api/client";
import type {
  Annotation,
  AnnotationBySenseLocation,
  CoreFullSense,
  CoreFullWord,
} from "../../api/types";
import {
  useWorkspace,
  workspace,
  type SelectionRange,
} from "../../state/useWorkspace";
import {
  LocationRow,
  stopLocationAction,
  useSenseLocations,
  type UsesStatus,
} from "../SenseUses";
import {
  SenseRowLabel,
  useLabelStore,
  type LabelStore,
} from "../Workspace/CoreRecordEditor";

interface Props {
  selection: SelectionRange;
  edition: string | null;
}

type Status = "idle" | "loading" | "ok" | "no-match" | "error";
type SenseCounts = Record<string, number>;

function useSenseCounts(words: CoreFullWord[]): SenseCounts | null {
  const [counts, setCounts] = useState<SenseCounts | null>(null);

  const senseUuids = useMemo(
    () => words.flatMap((w) => w.senses.map((s) => s.uuid)),
    [words],
  );

  useEffect(() => {
    if (senseUuids.length === 0) {
      setCounts({});
      return;
    }
    let cancelled = false;
    setCounts(null);
    getAnnotationSenseCounts(senseUuids)
      .then((r) => {
        if (!cancelled) setCounts(r.counts);
      })
      .catch(() => {
        if (!cancelled) setCounts({});
      });
    return () => {
      cancelled = true;
    };
  }, [senseUuids]);

  return counts;
}

function WhereUsedPanel({
  status,
  locations,
  error,
  selection,
  edition,
  word,
  sense,
  superEntryOrth,
}: {
  status: UsesStatus;
  locations: AnnotationBySenseLocation[];
  error: string | null;
  selection: SelectionRange;
  edition: string | null;
  word: CoreFullWord;
  sense: CoreFullSense;
  superEntryOrth: string;
}) {
  const blueskyStatus = useWorkspace((s) => s.blueskyStatus);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [postError, setPostError] = useState<string | null>(null);

  const canUse = blueskyStatus != null && edition != null && selection.anchorMarkerId != null;

  const onUse = async (loc: AnnotationBySenseLocation) => {
    if (!canUse || !edition || !selection.anchorMarkerId) return;
    const localId = loc.id ?? `${loc.text_id}:${loc.seq}:${loc.offset ?? 0}`;
    setBusyId(localId);
    setPostError(null);
    try {
      const length = Math.max(0, selection.end - selection.start);
      const result = await postAnnotation({
        text_id: selection.textid,
        edition,
        anchor: {
          marker_id: selection.anchorMarkerId,
          offset: selection.anchorOffset,
          length,
        },
        payload: {
          concept: word.concept ?? null,
          concept_id: word.concept_uuid ?? null,
          form: { orth: superEntryOrth, pron: word.pinyin },
          sense: {
            id: sense.uuid,
            pos: sense.pos,
            def_text: sense.def_text,
          },
          used_from: loc.id ?? null,
        },
      });
      const local: Annotation = {
        id: result.cid,
        offset: selection.start,
        bucket: selection.bucket,
        length,
        marker_id: selection.anchorMarkerId,
        concept: word.concept ?? undefined,
        concept_id: word.concept_uuid ?? undefined,
        form: { orth: superEntryOrth, pron: word.pinyin ?? undefined },
        sense: {
          id: sense.uuid,
          pos: sense.pos ?? undefined,
          def_text: sense.def_text ?? undefined,
        },
        metadata: { did: result.did, posted: "just now", used_from: loc.id ?? null },
      };
      workspace.prependLocalAnnotation(selection.textid, selection.seq, local);
      workspace.setCoreTarget(null);
    } catch (e) {
      setPostError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  if (status === "loading") return <div className="empty">Searching…</div>;
  if (status === "error") return <div className="empty">Failed: {error}</div>;
  if (locations.length === 0) {
    return <div className="empty">No prior uses of this sense.</div>;
  }
  return (
    <ul className="core-target-where-used">
      {locations.map((loc, i) => {
        const locKey = loc.id ?? `${loc.text_id}:${loc.seq}:${loc.offset ?? 0}`;
        const useAction = blueskyStatus != null && (
          <button
            type="button"
            className="core-target-where-action use"
            disabled={!canUse || busyId === locKey}
            onClick={(ev) => {
              stopLocationAction(ev);
              void onUse(loc);
            }}
            title="Post this sense for the current selection"
          >
            {busyId === locKey ? "Using…" : "Use"}
          </button>
        );
        return (
          <LocationRow
            key={loc.id ?? `${loc.text_id}:${loc.seq}:${i}`}
            loc={loc}
            extraAction={useAction}
          />
        );
      })}
      {postError && <li className="empty">Failed to post: {postError}</li>}
    </ul>
  );
}

function SenseRow({
  word,
  sense,
  superEntryUuid,
  superEntryOrth,
  useCount,
  selection,
  edition,
  store,
}: {
  word: CoreFullWord;
  sense: CoreFullSense;
  superEntryUuid: string;
  superEntryOrth: string;
  useCount: number | null;
  selection: SelectionRange;
  edition: string | null;
  store: LabelStore;
}) {
  const coreTarget = useWorkspace((s) => s.coreTarget);
  const [showWhere, setShowWhere] = useState(false);
  const uses = useSenseLocations(sense.uuid, showWhere);
  const selected = coreTarget?.sense.id === sense.uuid;

  const onPick = () => {
    if (selected) {
      workspace.setCoreTarget(null);
      return;
    }
    workspace.setCoreTarget({
      word_uuid: word.uuid,
      super_entry_uuid: superEntryUuid,
      concept: word.concept ?? null,
      concept_id: word.concept_uuid ?? null,
      form: { orth: superEntryOrth, pron: word.pinyin },
      sense: {
        id: sense.uuid,
        pos: sense.pos,
        def_text: sense.def_text,
      },
    });
  };

  return (
    <div className="core-target-sense-block">
      <div className={selected ? "core-target-row sense selected" : "core-target-row sense"}>
        <button
          type="button"
          className="core-target-sense-pick"
          onClick={onPick}
        >
          <span className="core-target-sense-def">
            <SenseRowLabel uuid={sense.uuid} store={store} />
          </span>
        </button>
        <button
          type="button"
          className="core-target-where-toggle"
          onClick={() => setShowWhere((v) => !v)}
          title="Show prior uses of this sense"
        >
          {showWhere ? "Hide" : `Uses ${useCount == null ? "…" : useCount}`}
        </button>
      </div>
      {showWhere && (
        <WhereUsedPanel
          status={uses.status}
          locations={uses.locations}
          error={uses.error}
          selection={selection}
          edition={edition}
          word={word}
          sense={sense}
          superEntryOrth={superEntryOrth}
        />
      )}
    </div>
  );
}

function WordRow({
  word,
  superEntryUuid,
  superEntryOrth,
  counts,
  selection,
  edition,
  store,
}: {
  word: CoreFullWord;
  superEntryUuid: string;
  superEntryOrth: string;
  counts: SenseCounts | null;
  selection: SelectionRange;
  edition: string | null;
  store: LabelStore;
}) {
  const coreTarget = useWorkspace((s) => s.coreTarget);
  const [expanded, setExpanded] = useState(false);

  const containsSelectedSense =
    coreTarget?.word_uuid === word.uuid && coreTarget?.sense.id != null;

  const concept = word.concept ?? "—";
  const pinyin = word.pinyin;
  const senseCount = word.senses.length;
  const attestedUses = word.senses.reduce(
    (n, s) => n + (counts?.[s.uuid] ?? 0),
    0,
  );

  return (
    <div className="core-target-group">
      <button
        type="button"
        className={
          containsSelectedSense
            ? "core-target-row word selected"
            : "core-target-row word"
        }
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="core-target-disclose">{expanded ? "▼" : "▶"}</span>
        <span className="core-target-label">
          <span className="core-target-orth">{superEntryOrth}</span>
          {pinyin && <span className="core-target-pinyin"> {pinyin}</span>}
          <span className="core-target-concept">: {concept}</span>
        </span>
        <span className="core-target-word-tally">
          {senseCount} senses · {counts == null ? "…" : attestedUses} uses
        </span>
      </button>
      {expanded && (
        <div className="core-target-senses">
          {word.senses.length === 0 && (
            <div className="empty">This word has no senses.</div>
          )}
          {word.senses.map((s) => (
            <SenseRow
              key={s.uuid}
              word={word}
              sense={s}
              superEntryUuid={superEntryUuid}
              superEntryOrth={superEntryOrth}
              useCount={counts == null ? null : counts[s.uuid] ?? 0}
              selection={selection}
              edition={edition}
              store={store}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function CoreTargetPicker({ selection, edition }: Props) {
  const [status, setStatus] = useState<Status>("idle");
  const [orth, setOrth] = useState<string>("");
  const [superEntryUuid, setSuperEntryUuid] = useState<string | null>(null);
  const [words, setWords] = useState<CoreFullWord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const counts = useSenseCounts(words);
  const labelStore = useLabelStore(new Map());

  const query = selection.chars.join("");
  const totalSenseCount = words.reduce((n, w) => n + w.senses.length, 0);
  const totalUseCount = words.reduce(
    (n, w) => n + w.senses.reduce((m, s) => m + (counts?.[s.uuid] ?? 0), 0),
    0,
  );

  useEffect(() => {
    if (!query) {
      setStatus("idle");
      setWords([]);
      setSuperEntryUuid(null);
      setOrth("");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    setError(null);
    setWords([]);
    setSuperEntryUuid(null);
    setOrth("");
    getCoreSuperEntryByOrthFull(query)
      .then((full) => {
        if (cancelled) return;
        setSuperEntryUuid(full.uuid);
        setOrth(full.orth);
        setWords(full.words);
        setStatus("ok");
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setStatus("no-match");
        } else {
          setError(String(e));
          setStatus("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [query]);

  if (!query) return null;

  return (
    <div className="core-target-picker">
      <div className="core-target-header">
        <span>Core matches</span>
        {status === "ok" && (
          <span className="core-target-total-tally">
            {totalSenseCount} senses · {counts == null ? "…" : totalUseCount} uses
          </span>
        )}
      </div>
      {status === "loading" && <div className="empty">Searching…</div>}
      {status === "no-match" && (
        <div className="empty">No core entry for "{query}".</div>
      )}
      {status === "error" && <div className="empty">Failed: {error}</div>}
      {status === "ok" && words.length === 0 && (
        <div className="empty">Super-entry has no word records.</div>
      )}
      {status === "ok" &&
        superEntryUuid &&
        words.map((w) => (
          <WordRow
            key={w.uuid}
            word={w}
            superEntryUuid={superEntryUuid}
            superEntryOrth={orth}
            counts={counts}
            selection={selection}
            edition={edition}
            store={labelStore}
          />
        ))}
    </div>
  );
}
