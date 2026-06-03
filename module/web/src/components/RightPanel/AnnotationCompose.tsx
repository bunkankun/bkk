import { useState } from "react";
import { postAnnotation } from "../../api/client";
import type { Annotation } from "../../api/types";
import {
  useWorkspace,
  workspace,
  type SelectionRange,
} from "../../state/useWorkspace";

interface Props {
  selection: SelectionRange;
  edition: string;
}

export function AnnotationCompose({ selection, edition }: Props) {
  const status = useWorkspace((s) => s.blueskyStatus);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status == null) return null;
  if (!selection.anchorMarkerId) return null;

  const length = Math.max(0, selection.end - selection.start);

  const onSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const trimmed = note.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      const result = await postAnnotation({
        text_id: selection.textid,
        edition,
        anchor: {
          marker_id: selection.anchorMarkerId!,
          offset: selection.anchorOffset,
          length,
        },
        payload: { note: trimmed },
      });
      const local: Annotation = {
        id: result.cid,
        offset: selection.start,
        bucket: selection.bucket,
        length,
        marker_id: selection.anchorMarkerId!,
        metadata: { note: trimmed, did: result.did, posted: "just now" },
      };
      workspace.prependLocalAnnotation(selection.textid, selection.seq, local);
      setNote("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="ann-compose" onSubmit={onSubmit}>
      <textarea
        className="ann-compose-note"
        placeholder="Post an annotation about this selection…"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={3}
      />
      {error && <div className="bsky-error">{error}</div>}
      <div className="ann-compose-actions">
        <button
          type="submit"
          className="sel-action"
          disabled={busy || note.trim().length === 0}
        >
          {busy ? "Posting…" : "Post annotation"}
        </button>
      </div>
    </form>
  );
}
