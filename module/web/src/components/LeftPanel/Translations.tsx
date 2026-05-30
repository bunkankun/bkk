import { useEffect, useState } from "react";
import { getBundleTranslations } from "../../api/client";
import type { TranslationSummary } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";

function isAiTranslation(item: TranslationSummary): boolean {
  if (/\bAI\b/.test(item.title ?? "")) return true;
  return item.responsibility.some((r) => /\bAI\b/.test(r.name ?? ""));
}

function names(t: TranslationSummary): string {
  return t.responsibility
    .map((r) => r.name)
    .filter((name): name is string => typeof name === "string" && name.length > 0)
    .join(", ");
}

function TranslationButton({ item, dimmed }: { item: TranslationSummary; dimmed: boolean }) {
  const selected = useWorkspace((s) => s.selectedTranslation?.id === item.id);
  return (
    <button
      className={`ov-row${selected ? " on" : ""}${dimmed ? " ov-row-dim" : ""}${isAiTranslation(item) ? " ov-row-ai" : ""}`}
      onClick={() => workspace.selectTranslation(item)}
      title={`${item.title ?? item.id}${dimmed ? " · no translation for this juan" : ""}`}
    >
      <span className="ov-title">{item.title ?? item.id}</span>
      <span className="ov-meta">
        {item.language ? `${item.language}` : ""}
        {names(item) ? ` · ${names(item)}` : ""}
        {item.date ? ` · ${item.date.slice(0, 4)}` : ""}
        {item.segment_count > 0 ? ` · ${item.segment_count} segs` : ""}
      </span>
    </button>
  );
}

export function Translations() {
  const activeTextid = useWorkspace((s) => s.activeTextid);
  const activeSeq = useWorkspace((s) => s.activeSeq);
  const [available, setAvailable] = useState<TranslationSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setAvailable([]);
    if (!activeTextid) return () => { cancelled = true; };
    getBundleTranslations(activeTextid)
      .then((res) => {
        if (!cancelled) setAvailable(res.translations);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => { cancelled = true; };
  }, [activeTextid]);

  if (!activeTextid) {
    return <div className="ov"><div className="ov-note">Open a text to see translations.</div></div>;
  }

  const byDate = (a: TranslationSummary, b: TranslationSummary) => {
    const da = a.date ?? "";
    const db = b.date ?? "";
    return da < db ? -1 : da > db ? 1 : 0;
  };

  const hasContent = (t: TranslationSummary) =>
    activeSeq == null || (t.source_juans ?? []).includes(activeSeq);

  const withContent = available.filter(hasContent).sort(byDate);
  const withoutContent = available.filter((t) => !hasContent(t)).sort(byDate);

  return (
    <div className="ov">
      <div className="ov-section">Translations for {activeTextid}</div>
      {error ? <div className="ov-note">{error}</div> : null}
      {available.length === 0 ? (
        <div className="ov-note">No translations available.</div>
      ) : (
        <>
          {withContent.map((item) => (
            <TranslationButton key={item.id} item={item} dimmed={false} />
          ))}
          {withoutContent.map((item) => (
            <TranslationButton key={item.id} item={item} dimmed={true} />
          ))}
        </>
      )}
    </div>
  );
}
