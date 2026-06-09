import { useWorkspace, workspace, type OpenMode, type Theme } from "../../state/useWorkspace";

export function Settings() {
  const theme = useWorkspace((s) => s.uiPrefs.theme);
  const openMode = useWorkspace((s) => s.openMode);
  const masterOnly = useWorkspace((s) => s.searchPrefs.masterOnly);
  const maxResults = useWorkspace((s) => s.searchPrefs.maxResults);

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
        <span className="settings-label">Open juan in</span>
        <select
          className="settings-select"
          value={openMode}
          title="Default mode when opening a juan"
          aria-label="Open juan in"
          onChange={(e) => workspace.setOpenMode(e.target.value as OpenMode)}
        >
          <option value="read">Read mode</option>
          <option value="trans">Translation mode</option>
          <option value="sticky">Same as current</option>
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
    </div>
  );
}
