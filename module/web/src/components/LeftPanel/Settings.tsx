import { useEffect, useState } from "react";
import {
  clearManifestCache,
  listUserTexts,
  syncUserTexts,
} from "../../api/client";
import type { UserTextListItem } from "../../api/types";
import { useWorkspace, workspace, type Theme } from "../../state/useWorkspace";
import { NewUserTextDialog } from "./NewUserTextDialog";

export function Settings() {
  const theme = useWorkspace((s) => s.uiPrefs.theme);
  const masterOnly = useWorkspace((s) => s.searchPrefs.masterOnly);
  const maxResults = useWorkspace((s) => s.searchPrefs.maxResults);
  const searchDistance = useWorkspace((s) => s.searchPrefs.searchDistance);
  const authenticated = useWorkspace((s) => s.auth.session?.authenticated === true);
  const [newTextOpen, setNewTextOpen] = useState(false);
  const [userTexts, setUserTexts] = useState<UserTextListItem[]>([]);
  const [userTextsLoading, setUserTextsLoading] = useState(false);
  const [userTextsError, setUserTextsError] = useState<string | null>(null);

  useEffect(() => {
    if (!authenticated) {
      setUserTexts([]);
      setUserTextsError(null);
      setUserTextsLoading(false);
      return;
    }
    let cancelled = false;
    setUserTextsLoading(true);
    setUserTextsError(null);
    void listUserTexts()
      .then((body) => {
        if (!cancelled) setUserTexts(body.texts);
      })
      .catch((err) => {
        if (!cancelled) setUserTextsError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setUserTextsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authenticated]);

  const refreshUserTexts = async () => {
    if (!authenticated) return;
    setUserTextsLoading(true);
    setUserTextsError(null);
    try {
      const body = await listUserTexts();
      setUserTexts(body.texts);
    } catch (err) {
      setUserTextsError(err instanceof Error ? err.message : String(err));
    } finally {
      setUserTextsLoading(false);
    }
  };

  const handleSyncUserTexts = async () => {
    if (!authenticated) return;
    setUserTextsLoading(true);
    setUserTextsError(null);
    try {
      await syncUserTexts();
      for (const item of userTexts) clearManifestCache(item.text_id);
      await refreshUserTexts();
    } catch (err) {
      setUserTextsError(err instanceof Error ? err.message : String(err));
    } finally {
      setUserTextsLoading(false);
    }
  };

  return (
    <div className="settings-panel">
      <label className="settings-row">
        <span className="settings-label">Theme</span>
        <select
          className="settings-select"
          value={theme}
          title="Theme"
          aria-label="Theme"
          onChange={(e) => workspace.setTheme(e.target.value as Theme)}
        >
          <option value="current">Default</option>
          <option value="dark">Dark</option>
          <option value="light">Light</option>
        </select>
      </label>
      <label className="settings-row">
        <span className="settings-label">Search: master only</span>
        <input
          type="checkbox"
          checked={masterOnly}
          title="Show only master-edition matches; hide witness variants"
          aria-label="Show only master matches"
          onChange={(e) => workspace.setMasterOnly(e.target.checked)}
        />
      </label>
      <label className="settings-row">
        <span className="settings-label">Search: max results</span>
        <input
          className="settings-select"
          type="number"
          min={100}
          max={200000}
          step={100}
          value={maxResults}
          title="When a query would exceed this many hits, fall back to the overview display"
          aria-label="Max results before overview"
          onChange={(e) => {
            const n = Number(e.target.value);
            if (Number.isFinite(n)) workspace.setMaxResults(n);
          }}
        />
      </label>
      <label className="settings-row">
        <span className="settings-label">Search: NEAR/NOT distance</span>
        <input
          className="settings-select"
          type="number"
          min={0}
          max={1000}
          step={1}
          value={searchDistance}
          title="Maximum character gap for TERM NEAR/NOT TERM searches"
          aria-label="NEAR and NOT search distance"
          onChange={(e) => {
            const n = Number(e.target.value);
            if (Number.isFinite(n)) workspace.setSearchDistance(n);
          }}
        />
      </label>
      <div className="settings-divider" />
      <div className="settings-row">
        <span className="settings-label">User texts</span>
        <div className="settings-actions">
          <button
            type="button"
            className="settings-action"
            disabled={!authenticated}
            title={authenticated ? "Import a new user text" : "Log in with GitHub to create user texts"}
            onClick={() => setNewTextOpen(true)}
          >
            New user text
          </button>
          <button
            type="button"
            className="settings-action"
            disabled={!authenticated || userTextsLoading}
            title="Refresh private texts from GitHub"
            onClick={() => void handleSyncUserTexts()}
          >
            Sync
          </button>
        </div>
      </div>
      {authenticated ? (
        <div className="settings-user-texts">
          {userTextsLoading ? <div className="settings-help">Loading user texts…</div> : null}
          {!userTextsLoading && userTexts.length === 0 ? (
            <div className="settings-help">No user texts yet.</div>
          ) : null}
          {userTexts.map((item) => (
            <div className="settings-user-text" key={item.text_id}>
              <div className="settings-user-text-meta">
                <span className="settings-user-text-id">{item.text_id}</span>
                <span className="settings-user-text-title">{item.title}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {userTextsError ? <p className="settings-help settings-error">{userTextsError}</p> : null}
      {!authenticated ? (
        <p className="settings-help">Log in with GitHub to create and read private user texts.</p>
      ) : (
        <p className="settings-help">Delete unwanted private text repositories on GitHub, then sync here.</p>
      )}
      <NewUserTextDialog
        open={newTextOpen}
        onClose={() => setNewTextOpen(false)}
        onCreated={() => void refreshUserTexts()}
      />
    </div>
  );
}
