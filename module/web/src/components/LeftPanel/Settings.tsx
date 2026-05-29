import { useWorkspace, workspace, type OpenMode, type Theme } from "../../state/useWorkspace";

export function Settings() {
  const theme = useWorkspace((s) => s.uiPrefs.theme);
  const openMode = useWorkspace((s) => s.openMode);

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
    </div>
  );
}
