import { useEffect } from "react";
import { getServerInfo } from "./api/client";
import { ActivityBar } from "./components/ActivityBar";
import { Catalog } from "./components/LeftPanel/Catalog";
import { Toc } from "./components/LeftPanel/Toc";
import { Menubar } from "./components/Menubar";
import { AnnotationsTab } from "./components/RightPanel/AnnotationsTab";
import { ChatTab } from "./components/RightPanel/ChatTab";
import { SearchTab } from "./components/RightPanel/SearchTab";
import { StatusBar } from "./components/StatusBar";
import { PaneTree } from "./components/Workspace/PaneTree";
import { useWorkspace, workspace } from "./state/useWorkspace";

function LeftPanel() {
  const activity = useWorkspace((s) => s.activity);
  return (
    <div className="lp">
      <div className="ph">
        <span>{activity === "catalog" ? "Catalog" : "Texts"}</span>
      </div>
      <div className="lp-body">
        {activity === "catalog" ? <Catalog /> : <Toc />}
      </div>
    </div>
  );
}

function RightPanel() {
  const tab = useWorkspace((s) => s.rightTab);
  const searchActive = useWorkspace((s) => s.search.status !== "idle");
  return (
    <div className="rp">
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
  useEffect(() => {
    let cancelled = false;
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

  return (
    <div className="app">
      <Menubar />
      <div className="app-main">
        <ActivityBar />
        <LeftPanel />
        <PaneTree />
        <RightPanel />
      </div>
      <StatusBar />
    </div>
  );
}
