import { startGithubLogin } from "../../api/client";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { SearchBar } from "./SearchBar";

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
        <span className="mb-logo-cjk">文勘君</span>
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
      {user && persistence.status !== "idle" ? (
        <span
          className={`mb-sync mb-sync-${persistence.status}`}
          title={persistence.error ?? "Workspace sync"}
        >
          {persistence.status === "error" ? "Sync error" : "Syncing"}
        </span>
      ) : null}
      {user ? (
        <div className="mb-user" title={`${user.workspace.repo} (${user.workspace.branch})`}>
          {user.avatar_url ? (
            <img className="mb-avatar" src={user.avatar_url} alt="" />
          ) : (
            <span className="mb-avatar-fallback">{user.login.slice(0, 1).toUpperCase()}</span>
          )}
          <span className="mb-user-name">{user.login}</span>
          <button className="mb-login" onClick={() => void workspace.logout()}>
            Logout
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
