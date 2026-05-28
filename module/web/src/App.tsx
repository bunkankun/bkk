import { useEffect } from "react";
import { getServerInfo } from "./api/client";
import { ActivityBar } from "./components/ActivityBar";
import { Catalog } from "./components/LeftPanel/Catalog";
import { Lists } from "./components/LeftPanel/Lists";
import { Toc } from "./components/LeftPanel/Toc";
import { Menubar } from "./components/Menubar";
import { AnnotationsTab } from "./components/RightPanel/AnnotationsTab";
import { ChatTab } from "./components/RightPanel/ChatTab";
import { SearchTab } from "./components/RightPanel/SearchTab";
import { StatusBar } from "./components/StatusBar";
import { PaneTree } from "./components/Workspace/PaneTree";
import { setResizing, useWorkspace, workspace } from "./state/useWorkspace";

function ResizeHandle({ side }: { side: "left" | "right" }) {
  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    // Clear any leftover text selection so the drag's terminating mouseup
    // can't be misread as a fresh drag-select by TextViewer.handleMouseUp.
    window.getSelection()?.removeAllRanges();
    setResizing(true);
    const startX = e.clientX;
    const startWidth = workspace.state.panelWidths[side];
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      const next = side === "left" ? startWidth + dx : startWidth - dx;
      workspace.setPanelWidth(side, next);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      // Defer clearing until after the bubbling mouseup has been observed
      // by element-level handlers (so the guard is still true when .ec's
      // onMouseUp fires).
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

function LeftPanel() {
  const activity = useWorkspace((s) => s.activity);
  const width = useWorkspace((s) => s.panelWidths.left);
  const title = activity === "timeline"
    ? "Timeline"
    : activity === "lists"
      ? "Lists"
    : activity === "catalog"
      ? "Catalog"
      : "Texts";
  return (
    <div className="lp" style={{ width }}>
      <div className="ph">
        <span>{title}</span>
      </div>
      <div className="lp-body">
        {activity === "timeline" ? (
          <Catalog mode="timeline" />
        ) : activity === "lists" ? (
          <Lists />
        ) : activity === "catalog" ? (
          <Catalog mode="categories" />
        ) : (
          <Toc />
        )}
      </div>
    </div>
  );
}

function RightPanel() {
  const tab = useWorkspace((s) => s.rightTab);
  const searchActive = useWorkspace((s) => s.search.status !== "idle");
  const width = useWorkspace((s) => s.panelWidths.right);
  return (
    <div className="rp" style={{ width }}>
      <div className="rt-bar">
        <button
          className={`rt${tab === "annotations" ? " on" : ""}`}
          onClick={() => workspace.setRightTab("annotations")}
        >
          Annot.
        </button>
        <button
          className={`rt${tab === "chat" ? " on" : ""}`}
          onClick={() => workspace.setRightTab("chat")}
        >
          Chat
        </button>
        {searchActive && (
          <button
            className={`rt${tab === "search" ? " on" : ""}`}
            onClick={() => workspace.setRightTab("search")}
          >
            Search
          </button>
        )}
      </div>
      {tab === "search" ? (
        <SearchTab />
      ) : tab === "chat" ? (
        <ChatTab />
      ) : (
        <AnnotationsTab />
      )}
    </div>
  );
}

export function App() {
  const theme = useWorkspace((s) => s.uiPrefs.theme);

  useEffect(() => {
    let cancelled = false;
    void workspace.loadAuthSession();
    getServerInfo()
      .then((info) => {
        if (cancelled) return;
        workspace.setServerInfo({
          upstream_repo: info.upstream_repo ?? null,
          version: info.version,
        });
      })
      .catch(() => {
        // best-effort: leave serverInfo null if backend isn't up
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div className="app">
      <Menubar />
      <div className="app-main">
        <ActivityBar />
        <LeftPanel />
        <ResizeHandle side="left" />
        <PaneTree />
        <ResizeHandle side="right" />
        <RightPanel />
      </div>
      <StatusBar />
    </div>
  );
}
