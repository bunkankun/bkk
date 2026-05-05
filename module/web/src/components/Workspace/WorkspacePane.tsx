import { useWorkspace } from "../../state/useWorkspace";
import { CharInfoBar } from "../CharInfoBar";
import { TextViewer } from "./TextViewer";

export function WorkspacePane() {
  const pane = useWorkspace((s) => s.pane);
  const activeTab =
    pane.tabs.find((t) => t.id === pane.activeTabId) ?? pane.tabs[0] ?? null;

  return (
    <div className="wp">
      <div className="tab-bar">
        {pane.tabs.length === 0 && (
          <div
            style={{
              padding: "0 12px",
              color: "var(--t3)",
              alignSelf: "center",
              fontSize: 11,
            }}
          >
            (no open text)
          </div>
        )}
        {pane.tabs.map((t) => (
          <button
            key={t.id}
            className={`tab${t.id === activeTab?.id ? " on" : ""}`}
          >
            {t.textid} · juan {t.seq}
          </button>
        ))}
      </div>
      {activeTab ? (
        <TextViewer
          key={`${activeTab.textid}:${activeTab.seq}`}
          textid={activeTab.textid}
          seq={activeTab.seq}
        />
      ) : (
        <div className="empty-pane">Select a text from the catalog or TOC.</div>
      )}
      <CharInfoBar />
    </div>
  );
}
