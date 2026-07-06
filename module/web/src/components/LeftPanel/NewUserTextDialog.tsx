import { useEffect, useRef, useState } from "react";
import { createUserText, previewUserText } from "../../api/client";
import type {
  UserTextFormat,
  UserTextPreview,
  UserTextSourceFile,
} from "../../api/types";
import { workspace } from "../../state/useWorkspace";

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated?: () => void;
};

type Status = "input" | "previewing" | "confirm" | "creating";

export function NewUserTextDialog({ open, onClose, onCreated }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [format, setFormat] = useState<UserTextFormat>("krp");
  const [paste, setPaste] = useState("");
  const [files, setFiles] = useState<UserTextSourceFile[]>([]);
  const [status, setStatus] = useState<Status>("input");
  const [preview, setPreview] = useState<UserTextPreview | null>(null);
  const [textId, setTextId] = useState("");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && !dialogRef.current?.open) dialogRef.current?.showModal();
    if (!open && dialogRef.current?.open) dialogRef.current.close();
  }, [open]);

  const reset = () => {
    setStatus("input");
    setPreview(null);
    setTextId("");
    setTitle("");
    setAuthor("");
    setNotes("");
    setError(null);
  };

  const close = () => {
    dialogRef.current?.close();
    reset();
    onClose();
  };

  const readFiles = async (selected: FileList | null) => {
    if (!selected) return;
    setError(null);
    try {
      setFiles(await Promise.all(
        Array.from(selected).map(async (file) => ({
          name: file.name,
          content: await file.text(),
        })),
      ));
      setPaste("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const runPreview = async () => {
    setError(null);
    setStatus("previewing");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 90_000);
    try {
      const result = await previewUserText({
        format,
        ...(files.length ? { files } : { paste }),
      }, controller.signal);
      setPreview(result);
      setTextId(result.suggested_text_id);
      setTitle(result.title || "");
      setStatus("confirm");
    } catch (e) {
      setError(
        e instanceof DOMException && e.name === "AbortError"
          ? "Validation timed out after 90 seconds. Check the server log or split very large source files."
          : e instanceof Error ? e.message : String(e),
      );
      setStatus("input");
    } finally {
      window.clearTimeout(timeout);
    }
  };

  const create = async () => {
    if (!preview) return;
    setError(null);
    setStatus("creating");
    try {
      const result = await createUserText({
        preview_token: preview.preview_token,
        text_id: textId.trim(),
        title: title.trim(),
        author: author.trim() || undefined,
        notes: notes.trim() || undefined,
      });
      onCreated?.();
      close();
      workspace.openJuan(result.text_id, result.first_seq);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("confirm");
    }
  };

  const hasSource = files.length > 0 || paste.trim().length > 0;

  return (
    <dialog
      ref={dialogRef}
      className="user-text-dialog"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <div className="user-text-head">
        <h2>New user text</h2>
        <button type="button" onClick={close} aria-label="Close">×</button>
      </div>

      {status === "input" || status === "previewing" ? (
        <div className="user-text-form">
          <label>
            <span>Source format</span>
            <select value={format} onChange={(e) => setFormat(e.target.value as UserTextFormat)}>
              <option value="krp">KRP</option>
              <option value="tls">TLS</option>
              <option value="cbeta">CBETA</option>
            </select>
          </label>
          <label>
            <span>Paste source</span>
            <textarea
              rows={10}
              value={paste}
              disabled={files.length > 0}
              onChange={(e) => setPaste(e.target.value)}
              placeholder="Paste one UTF-8 source document"
            />
          </label>
          <div className="user-text-or">or</div>
          <label>
            <span>Upload source files</span>
            <input
              type="file"
              multiple={format !== "cbeta"}
              accept={format === "krp" ? ".txt,text/plain" : ".xml,text/xml,application/xml"}
              onChange={(e) => void readFiles(e.target.files)}
            />
          </label>
          {files.length > 0 ? (
            <div className="user-text-files">
              {files.map((file) => file.name).join(", ")}
              <button type="button" onClick={() => setFiles([])}>Clear</button>
            </div>
          ) : null}
          <div className="user-text-actions">
            <button type="button" onClick={close}>Cancel</button>
            <button
              type="button"
              className="primary"
              disabled={!hasSource || status === "previewing"}
              onClick={() => void runPreview()}
            >
              {status === "previewing" ? "Validating…" : "Continue"}
            </button>
          </div>
        </div>
      ) : (
        <div className="user-text-form">
          <div className="user-text-summary">
            {preview?.source_files.join(", ")}
            {preview?.substitution_count
              ? ` · ${preview.substitution_count} canonical substitution(s)`
              : " · no canonical substitutions"}
          </div>
          <label>
            <span>Text ID</span>
            <input value={textId} onChange={(e) => setTextId(e.target.value)} />
          </label>
          <label>
            <span>Title</span>
            <input value={title} required onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            <span>Author (optional)</span>
            <input value={author} onChange={(e) => setAuthor(e.target.value)} />
          </label>
          <label>
            <span>Notes (optional)</span>
            <textarea rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          {preview?.findings.length ? (
            <div className="user-text-warnings">
              {preview.findings.map((finding) => (
                <div key={`${finding.rule_id}:${finding.path}`}>{finding.message}</div>
              ))}
            </div>
          ) : null}
          <div className="user-text-actions">
            <button type="button" disabled={status === "creating"} onClick={() => setStatus("input")}>
              Back
            </button>
            <button
              type="button"
              className="primary"
              disabled={!textId.trim() || !title.trim() || status === "creating"}
              onClick={() => void create()}
            >
              {status === "creating" ? "Creating repository…" : "Create text"}
            </button>
          </div>
        </div>
      )}
      {error ? <div className="user-text-error">{error}</div> : null}
    </dialog>
  );
}
