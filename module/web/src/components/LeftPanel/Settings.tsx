import { useWorkspace, workspace, type Theme } from "../../state/useWorkspace";

export function Settings() {
  const theme = useWorkspace((s) => s.uiPrefs.theme);

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
    </div>
  );
}
