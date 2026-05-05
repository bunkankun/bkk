import { useWorkspace } from "../../state/useWorkspace";
import { SearchBar } from "./SearchBar";

export function Menubar() {
  const upstream = useWorkspace((s) => s.serverInfo?.upstream_repo);
  const version = useWorkspace((s) => s.serverInfo?.version);
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
      <button className="mb-login" disabled title="v2">
        Login
      </button>
    </div>
  );
}
