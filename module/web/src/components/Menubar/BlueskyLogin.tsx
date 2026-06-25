import { useEffect, useRef, useState } from "react";
import {
  deleteBlueskySession,
  getBlueskyStatus,
  postBlueskyLogin,
} from "../../api/client";
import { useWorkspace, workspace } from "../../state/useWorkspace";

export function BlueskyLogin() {
  const status = useWorkspace((s) => s.blueskyStatus);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBlueskyStatus()
      .then((s) => {
        if (cancelled) return;
        if (s.handle && s.did) {
          workspace.setBlueskyStatus({
            handle: s.handle,
            did: s.did,
            avatar_url: s.avatar_url ?? null,
          });
        } else {
          workspace.setBlueskyStatus(null);
        }
      })
      .catch(() => {
        /* not logged in or backend offline — leave as null */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const openDialog = () => {
    setError(null);
    dialogRef.current?.showModal();
  };

  const closeDialog = () => {
    dialogRef.current?.close();
  };

  const onSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!handle || !password) return;
    setBusy(true);
    setError(null);
    try {
      const s = await postBlueskyLogin({ handle, app_password: password });
      if (s.handle && s.did) {
        workspace.setBlueskyStatus({
          handle: s.handle,
          did: s.did,
          avatar_url: s.avatar_url ?? null,
        });
      }
      setPassword("");
      closeDialog();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDisconnect = async () => {
    try {
      await deleteBlueskySession();
    } catch {
      /* ignore */
    }
    workspace.setBlueskyStatus(null);
  };

  return (
    <>
      {status ? (
        <div className="mb-bsky mb-user" title={`Bluesky: ${status.handle}`}>
          {status.avatar_url ? (
            <img className="mb-avatar" src={status.avatar_url} alt="" />
          ) : (
            <span className="mb-avatar-fallback">
              {status.handle.slice(0, 1).toUpperCase()}
            </span>
          )}
          <button
            type="button"
            className="mb-user-name"
            onClick={onDisconnect}
            title="Click here to disconnect Bluesky"
          >
            {status.handle.endsWith(".bsky.social")
              ? status.handle.slice(0, -".bsky.social".length)
              : status.handle}
          </button>
        </div>
      ) : (
        <button
          className="mb-login"
          onClick={openDialog}
          title="Login with Bluesky"
        >
          Bluesky Login
        </button>
      )}
      <dialog ref={dialogRef} className="mb-bsky-dialog">
        <form className="bsky-panel form" onSubmit={onSubmit}>
          <div className="bsky-label">
            Enter your Bluesky handle and an{" "}
            <a
              href="https://bsky.app/settings/app-passwords"
              target="_blank"
              rel="noreferrer"
            >
              app password
            </a>
            . The session lives in memory only and is lost on server restart.
          </div>
          <input
            type="text"
            placeholder="handle (e.g. you.bsky.social)"
            value={handle}
            onChange={(e) => setHandle(e.target.value)}
            autoComplete="username"
          />
          <input
            type="password"
            placeholder="app password (xxxx-xxxx-xxxx-xxxx)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="off"
          />
          {error && <div className="bsky-error">{error}</div>}
          <div className="bsky-form-actions">
            <button type="submit" className="sel-action" disabled={busy}>
              {busy ? "Connecting…" : "Connect"}
            </button>
            <button
              type="button"
              className="sel-clear"
              onClick={closeDialog}
              disabled={busy}
            >
              cancel
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}
