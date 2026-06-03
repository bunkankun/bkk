import { useEffect, useState } from "react";
import {
  deleteBlueskySession,
  getBlueskyStatus,
  postBlueskyLogin,
} from "../../api/client";
import { useWorkspace, workspace } from "../../state/useWorkspace";

export function BlueskyPanel() {
  const status = useWorkspace((s) => s.blueskyStatus);
  const [expanded, setExpanded] = useState(false);
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
          workspace.setBlueskyStatus({ handle: s.handle, did: s.did });
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

  const onSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!handle || !password) return;
    setBusy(true);
    setError(null);
    try {
      const s = await postBlueskyLogin({ handle, app_password: password });
      if (s.handle && s.did) {
        workspace.setBlueskyStatus({ handle: s.handle, did: s.did });
      }
      setPassword("");
      setExpanded(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDisconnect = async () => {
    setBusy(true);
    try {
      await deleteBlueskySession();
    } catch {
      /* ignore */
    }
    workspace.setBlueskyStatus(null);
    setBusy(false);
  };

  if (status) {
    return (
      <div className="bsky-panel connected">
        <span className="bsky-handle">{status.handle}</span>
        <span className="bsky-label">connected to Bluesky</span>
        <button className="sel-clear" onClick={onDisconnect} disabled={busy}>
          disconnect
        </button>
      </div>
    );
  }

  if (!expanded) {
    return (
      <div className="bsky-panel disconnected">
        <button
          className="sel-action"
          onClick={() => setExpanded(true)}
        >
          Connect Bluesky to post annotations
        </button>
      </div>
    );
  }

  return (
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
          onClick={() => setExpanded(false)}
          disabled={busy}
        >
          cancel
        </button>
      </div>
    </form>
  );
}
