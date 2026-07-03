import { useEffect } from "react";
import { getServerInfo } from "./api/client";
import { ActivityBar } from "./components/ActivityBar";
import { Admin } from "./components/LeftPanel/Admin";
import { Catalog } from "./components/LeftPanel/Catalog";
import { Core } from "./components/LeftPanel/Core";
import { EditingTasks } from "./components/LeftPanel/EditingTasks";
import { History } from "./components/LeftPanel/History";
import { Lists } from "./components/LeftPanel/Lists";
import { Translations } from "./components/LeftPanel/Translations";
import { Settings } from "./components/LeftPanel/Settings";
import { Toc } from "./components/LeftPanel/Toc";
import { Menubar } from "./components/Menubar";
import { AnnotationsTab } from "./components/RightPanel/AnnotationsTab";
import { ChatTab } from "./components/RightPanel/ChatTab";
import { ParallelsTab } from "./components/RightPanel/ParallelsTab";
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
    : activity === "overlays"
      ? "TRANSLATIONS"
    : activity === "lists"
      ? "Lists"
    : activity === "history"
      ? "History"
    : activity === "settings"
      ? "Settings"
    : activity === "admin"
      ? "Admin"
    : activity === "edit"
      ? "Editing Tasks"
    : activity === "core"
      ? "TLS"
    : activity === "catalog"
      ? "Catalog"
      : "Contents";
  return (
    <div className="lp" style={{ width }}>
      <div className="ph">
        <span>{title}</span>
      </div>
      <div className="lp-body">
        {activity === "timeline" ? (
          <Catalog mode="timeline" />
        ) : activity === "overlays" ? (
          <Translations />
        ) : activity === "lists" ? (
          <Lists />
        ) : activity === "history" ? (
          <History />
        ) : activity === "settings" ? (
          <Settings />
        ) : activity === "admin" ? (
          <Admin />
        ) : activity === "edit" ? (
          <EditingTasks />
        ) : activity === "core" ? (
          <Core />
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
  const blueskyEnabled = useWorkspace((s) => s.serverInfo?.bluesky_enabled === true);
  const parallelsEnabled = useWorkspace((s) => s.serverInfo?.parallels_enabled === true);
  const parallelsSource = useWorkspace((s) => s.parallelsSource);
  const activeTextid = useWorkspace((s) => s.activeTextid);
  const activeSeq = useWorkspace((s) => s.activeSeq);
  const width = useWorkspace((s) => s.panelWidths.right);
  const effectiveTab =
    (tab === "chat" && !blueskyEnabled) || (tab === "parallels" && !parallelsEnabled)
      ? "annotations"
      : tab;
  return (
    <div className="rp" style={{ width }}>
      <div className="rt-bar">
        <button
          className={`rt${effectiveTab === "annotations" ? " on" : ""}`}
          onClick={() => workspace.setRightTab("annotations")}
        >
          Annot.
        </button>
        {parallelsEnabled && (
          <button
            className={`rt${effectiveTab === "parallels" ? " on" : ""}`}
            onClick={() => {
              if (parallelsSource == null && activeTextid != null && activeSeq != null) {
                workspace.openParallelsPanel(activeTextid, activeSeq);
                return;
              }
              workspace.setRightTab("parallels");
            }}
          >
            Parall.
          </button>
        )}
        {blueskyEnabled && (
          <button
            className={`rt${effectiveTab === "chat" ? " on" : ""}`}
            onClick={() => workspace.setRightTab("chat")}
          >
            Chat
          </button>
        )}
        {searchActive && (
          <button
            className={`rt${effectiveTab === "search" ? " on" : ""}`}
            onClick={() => workspace.setRightTab("search")}
          >
            Search
          </button>
        )}
      </div>
      {effectiveTab === "search" ? (
        <SearchTab />
      ) : effectiveTab === "parallels" ? (
        <ParallelsTab />
      ) : effectiveTab === "chat" ? (
        <ChatTab />
      ) : (
        <AnnotationsTab />
      )}
    </div>
  );
}

export function App() {
  const theme = useWorkspace((s) => s.uiPrefs.theme);
  const leftSidebarVisible = useWorkspace((s) => s.uiPrefs.leftSidebarVisible);
  const rightSidebarVisible = useWorkspace((s) => s.uiPrefs.rightSidebarVisible);

  useEffect(() => {
    let cancelled = false;
    void workspace.loadAuthSession();
    getServerInfo()
      .then((info) => {
        if (cancelled) return;
        workspace.setServerInfo({
          upstream_repo: info.upstream_repo ?? null,
          version: info.version,
          bluesky_enabled: info.bluesky_enabled === true,
          parallels_enabled: info.parallels_enabled === true,
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
        {leftSidebarVisible && (
          <>
            <LeftPanel />
            <ResizeHandle side="left" />
          </>
        )}
        <PaneTree />
        {rightSidebarVisible && (
          <>
            <ResizeHandle side="right" />
            <RightPanel />
          </>
        )}
      </div>
      <StatusBar />
    </div>
  );
}
