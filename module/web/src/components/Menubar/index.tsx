import { startGithubLogin } from "../../api/client";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { BlueskyLogin } from "./BlueskyLogin";
import { SearchBar } from "./SearchBar";

function SidebarToggles() {
  const leftVisible = useWorkspace((s) => s.uiPrefs.leftSidebarVisible);
  const rightVisible = useWorkspace((s) => s.uiPrefs.rightSidebarVisible);
  return (
    <div className="mb-sidebar-toggles" aria-label="Sidebar visibility">
      <button
        type="button"
        className={`mb-icon-btn${leftVisible ? " on" : ""}`}
        onClick={() => workspace.toggleSidebar("left")}
        title={leftVisible ? "Hide left sidebar" : "Show left sidebar"}
        aria-pressed={leftVisible}
      >
        <svg width={15} height={15} viewBox="0 0 15 15" fill="none" aria-hidden="true">
          <rect x="2" y="2.5" width="11" height="10" rx="1.2" stroke="currentColor" />
          <path d="M5.5 3v9" stroke="currentColor" />
        </svg>
      </button>
      <button
        type="button"
        className={`mb-icon-btn${rightVisible ? " on" : ""}`}
        onClick={() => workspace.toggleSidebar("right")}
        title={rightVisible ? "Hide right sidebar" : "Show right sidebar"}
        aria-pressed={rightVisible}
      >
        <svg width={15} height={15} viewBox="0 0 15 15" fill="none" aria-hidden="true">
          <rect x="2" y="2.5" width="11" height="10" rx="1.2" stroke="currentColor" />
          <path d="M9.5 3v9" stroke="currentColor" />
        </svg>
      </button>
    </div>
  );
}

export function Menubar() {
  const upstream = useWorkspace((s) => s.serverInfo?.upstream_repo);
  const version = useWorkspace((s) => s.serverInfo?.version);
  const auth = useWorkspace((s) => s.auth);
  const persistence = useWorkspace((s) => s.persistence);
  const user = auth.session?.user ?? null;
  const tooltip =
    upstream != null
      ? `Bunkankun${version ? ` v${version}` : ""} — powered by ${upstream}`
      : `Bunkankun${version ? ` v${version}` : ""}`;
  return (
    <div className="mb">
      <div className="mb-logo" title={tooltip}>
        <span className="mb-logo-cjk mb-logo-cjk-full">文看訓</span>
        <span className="mb-logo-cjk mb-logo-cjk-compact">文訓</span>
        <span className="mb-logo-cjk mb-logo-cjk-min">訓</span>
        <span className="mb-logo-txt">Bunkankun</span>
      </div>
      <div className="mb-sep" />
      <button className="mb-btn" disabled title="v2">
        Browse
      </button>
      <button className="mb-btn" disabled title="v2">
        Bookmarks
      </button>
      <button className="mb-btn" disabled title="v2">
        Help
      </button>
      <div className="mb-spacer" />
      <SearchBar />
      <SidebarToggles />
      {user && persistence.status !== "idle" ? (
        <span
          className={`mb-sync mb-sync-${persistence.status}`}
          title={persistence.error ?? "Workspace sync"}
        >
          {persistence.status === "error" ? "Sync error" : "Syncing"}
        </span>
      ) : null}
      <BlueskyLogin />
      {user ? (
        <div className="mb-user" title={`${user.workspace.repo} (${user.workspace.branch})`}>
          {user.avatar_url ? (
            <img className="mb-avatar" src={user.avatar_url} alt="" />
          ) : (
            <span className="mb-avatar-fallback">{user.login.slice(0, 1).toUpperCase()}</span>
          )}
          <button
            type="button"
            className="mb-user-name"
            onClick={() => void workspace.logout()}
            title="Click here to log out"
          >
            {user.login}
          </button>
        </div>
      ) : (
        <button
          className="mb-login"
          onClick={startGithubLogin}
          disabled={auth.status === "loading"}
          title={auth.error ?? "Login with GitHub"}
        >
          GitHub Login
        </button>
      )}
    </div>
  );
}
