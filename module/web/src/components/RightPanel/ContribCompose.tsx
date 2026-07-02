import { useState } from "react";
import { postComment, postTranslation } from "../../api/client";
import {
  useWorkspace,
  type SelectionRange,
} from "../../state/useWorkspace";

type Kind = "comment" | "translation";

interface Props {
  selection: SelectionRange;
  edition: string;
}

// Minimal verification surface for the new comment + translation lexicons.
// Full UI lives in follow-up tasks; this just exercises the two endpoints
// end-to-end so the round-trip with the harvester can be tested manually.
export function ContribCompose({ selection, edition }: Props) {
  const blueskyEnabled = useWorkspace((s) => s.serverInfo?.bluesky_enabled === true);
  const status = useWorkspace((s) => s.blueskyStatus);
  const [kind, setKind] = useState<Kind>("comment");
  const [body, setBody] = useState("");
  const [lang, setLang] = useState("en");
  const [translationId, setTranslationId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  if (!blueskyEnabled) return null;
  if (status == null) return null;
  if (!selection.anchorMarkerId) return null;

  const length = Math.max(0, selection.end - selection.start);
  const anchor = {
    marker_id: selection.anchorMarkerId,
    offset: selection.anchorOffset,
    length,
  };

  const reset = () => {
    setBody("");
    setOkMsg(null);
  };

  const onSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const trimmed = body.trim();
    if (!trimmed) return;
    if (kind === "translation" && !translationId.trim()) {
      setError("translation_id is required");
      return;
    }
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const result =
        kind === "comment"
          ? await postComment({
              text_id: selection.textid,
              edition,
              anchor,
              body: trimmed,
              lang,
            })
          : await postTranslation({
              text_id: selection.textid,
              edition,
              anchor,
              translation_id: translationId.trim(),
              text: trimmed,
              lang,
            });
      setOkMsg(`Posted ${kind} · ${result.cid.slice(0, 12)}…`);
      reset();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="ann-compose" onSubmit={onSubmit}>
      <div className="ann-compose-actions">
        <label className="sel-pin-toggle">
          <input
            type="radio"
            name="contrib-kind"
            checked={kind === "comment"}
            onChange={() => setKind("comment")}
          />
          Comment
        </label>
        <label className="sel-pin-toggle">
          <input
            type="radio"
            name="contrib-kind"
            checked={kind === "translation"}
            onChange={() => setKind("translation")}
          />
          Translation
        </label>
      </div>
      {kind === "translation" && (
        <input
          className="ann-compose-note"
          placeholder="translation_id (bundle id, e.g. KR1h0004-en-…)"
          value={translationId}
          onChange={(e) => setTranslationId(e.target.value)}
        />
      )}
      <textarea
        className="ann-compose-note"
        placeholder={
          kind === "comment"
            ? "Markdown comment about this selection…"
            : "Translation of this span…"
        }
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
      />
      <div className="ann-compose-actions">
        <input
          className="ann-compose-note"
          style={{ maxWidth: "6em" }}
          placeholder="lang"
          value={lang}
          onChange={(e) => setLang(e.target.value)}
        />
        <button
          type="submit"
          className="sel-action"
          disabled={busy || body.trim().length === 0}
        >
          {busy ? "Posting…" : `Post ${kind}`}
        </button>
      </div>
      {error && <div className="bsky-error">{error}</div>}
      {okMsg && <div className="ann-def">{okMsg}</div>}
    </form>
  );
}
