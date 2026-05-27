import { useEffect, useState } from "react";
import { getManifest } from "../../api/client";
import { setResizing, useWorkspace, workspace } from "../../state/useWorkspace";
import { CharInfoBar } from "../CharInfoBar";
import { ImagePanel } from "./ImagePanel";
import { TextViewer } from "./TextViewer";

function InspectResizer() {
  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    window.getSelection()?.removeAllRanges();
    setResizing(true);
    const startX = e.clientX;
    const startWidth = workspace.state.panelWidths.inspect;
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      workspace.setPanelWidth("inspect", startWidth - dx);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setTimeout(() => setResizing(false), 0);
    };
    document.body.style.cursor = "ew-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };
  return (
    <div
      className="pane-resize"
      role="separator"
      aria-orientation="vertical"
      onMouseDown={onMouseDown}
    />
  );
}

export function WorkspacePane() {
  const pane = useWorkspace((s) => s.pane);
  const readMode = useWorkspace((s) => s.readMode);
  const inspectWidth = useWorkspace((s) => s.panelWidths.inspect);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const activeTab =
    pane.tabs.find((t) => t.id === pane.activeTabId) ?? pane.tabs[0] ?? null;

  const showInspect = readMode === "inspect" && activeTab != null;

  useEffect(() => {
    let cancelled = false;
    const missing = [...new Set(pane.tabs.map((t) => t.textid))].filter(
      (textid) => titles[textid] == null,
    );
    if (missing.length === 0) return;
    Promise.all(
      missing.map((textid) =>
        getManifest(textid)
          .then((m) => [textid, m.metadata?.title ?? textid] as const)
          .catch(() => [textid, textid] as const),
      ),
    ).then((entries) => {
      if (cancelled) return;
      setTitles((prev) => {
        const next = { ...prev };
        for (const [textid, title] of entries) next[textid] = title;
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [pane.tabs, titles]);

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
            title={`${titles[t.textid] ?? t.textid} · ${t.textid} · 卷 ${t.seq}`}
          >
            {titles[t.textid] ?? t.textid} · {t.textid} · 卷 {t.seq}
          </button>
        ))}
      </div>
      {activeTab ? (
        showInspect ? (
          <div className="ws-split">
            <div className="ws-split-left">
              <TextViewer
                key={`${activeTab.textid}:${activeTab.seq}`}
                textid={activeTab.textid}
                seq={activeTab.seq}
              />
            </div>
            <InspectResizer />
            <div className="ws-split-right" style={{ width: inspectWidth }}>
              <ImagePanel
                key={`${activeTab.textid}:${activeTab.seq}`}
                textid={activeTab.textid}
                seq={activeTab.seq}
              />
            </div>
          </div>
        ) : (
          <TextViewer
            key={`${activeTab.textid}:${activeTab.seq}`}
            textid={activeTab.textid}
            seq={activeTab.seq}
          />
        )
      ) : (
        <div className="empty-pane">Select a text from the catalog or TOC.</div>
      )}
      <CharInfoBar />
    </div>
  );
}
