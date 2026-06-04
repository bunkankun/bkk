import { useEffect, useState } from "react";
import {
  ApiError,
  getAnnotationsBySense,
  getCoreSuperEntryByOrthFull,
} from "../../api/client";
import type {
  AnnotationBySenseLocation,
  CoreFullSense,
  CoreFullWord,
} from "../../api/types";
import {
  useWorkspace,
  workspace,
  type SelectionRange,
} from "../../state/useWorkspace";

interface Props {
  selection: SelectionRange;
}

type Status = "idle" | "loading" | "ok" | "no-match" | "error";

function senseSummary(sense: CoreFullSense): string {
  const bits: string[] = [];
  if (sense.pos) bits.push(sense.pos);
  if (sense.syn_func) bits.push(sense.syn_func);
  if (sense.sem_feat) bits.push(sense.sem_feat);
  if (bits.length === 0 && sense.body_number != null) {
    bits.push(`sense ${sense.body_number}`);
  }
  return bits.join(" · ");
}

function WhereUsedPanel({ senseUuid }: { senseUuid: string }) {
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  const [locations, setLocations] = useState<AnnotationBySenseLocation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    getAnnotationsBySense(senseUuid)
      .then((r) => {
        if (cancelled) return;
        setLocations(r.locations);
        setStatus("ok");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e));
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [senseUuid]);

  if (status === "loading") return <div className="empty">Searching…</div>;
  if (status === "error") return <div className="empty">Failed: {error}</div>;
  if (locations.length === 0) {
    return <div className="empty">No prior uses of this sense.</div>;
  }
  return (
    <ul className="core-target-where-used">
      {locations.map((loc, i) => (
        <li key={loc.id ?? `${loc.text_id}:${loc.seq}:${i}`}>
          <span className="core-target-where-loc">
            {loc.text_id} · juan {loc.seq}
            {loc.orth ? ` · ${loc.orth}` : ""}
          </span>
          {loc.note && <span className="core-target-where-note">{loc.note}</span>}
        </li>
      ))}
    </ul>
  );
}

function SenseRow({
  word,
  sense,
  superEntryUuid,
  superEntryOrth,
}: {
  word: CoreFullWord;
  sense: CoreFullSense;
  superEntryUuid: string;
  superEntryOrth: string;
}) {
  const coreTarget = useWorkspace((s) => s.coreTarget);
  const [showWhere, setShowWhere] = useState(false);
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
        syn_func: sense.syn_func,
      },
    });
  };

  const summary = senseSummary(sense);

  return (
    <div className="core-target-sense-block">
      <div className={selected ? "core-target-row sense selected" : "core-target-row sense"}>
        <button
          type="button"
          className="core-target-sense-pick"
          onClick={onPick}
        >
          {summary && <span className="core-target-sense-summary">{summary}</span>}
          {sense.def && <span className="core-target-sense-def">{sense.def}</span>}
          {!summary && !sense.def && (
            <span className="core-target-sense-summary">{sense.uuid.slice(0, 8)}</span>
          )}
        </button>
        <button
          type="button"
          className="core-target-where-toggle"
          onClick={() => setShowWhere((v) => !v)}
          title="Show prior uses of this sense"
        >
          {showWhere ? "Hide uses" : "Where used"}
        </button>
      </div>
      {showWhere && <WhereUsedPanel senseUuid={sense.uuid} />}
    </div>
  );
}

function WordRow({
  word,
  superEntryUuid,
  superEntryOrth,
}: {
  word: CoreFullWord;
  superEntryUuid: string;
  superEntryOrth: string;
}) {
  const coreTarget = useWorkspace((s) => s.coreTarget);
  const [expanded, setExpanded] = useState(false);

  const containsSelectedSense =
    coreTarget?.word_uuid === word.uuid && coreTarget?.sense.id != null;

  const concept = word.concept ?? "—";
  const pinyin = word.pinyin;

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
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function CoreTargetPicker({ selection }: Props) {
  const [status, setStatus] = useState<Status>("idle");
  const [orth, setOrth] = useState<string>("");
  const [superEntryUuid, setSuperEntryUuid] = useState<string | null>(null);
  const [words, setWords] = useState<CoreFullWord[]>([]);
  const [error, setError] = useState<string | null>(null);

  const query = selection.chars.join("");

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
      <div className="core-target-header">Core matches</div>
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
          />
        ))}
    </div>
  );
}
