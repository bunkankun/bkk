import type { SearchSort } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import type { SearchTarget } from "../../state/useWorkspace";

const TARGETS: { value: SearchTarget; label: string; v2: boolean }[] = [
  { value: "fulltext", label: "Full text", v2: false },
  { value: "dictionary", label: "Dictionary", v2: true },
  { value: "translations", label: "Translations", v2: true },
];

const SORTS: { value: SearchSort; label: string }[] = [
  { value: "match", label: "by match" },
  { value: "textid", label: "by text id" },
  { value: "reverse_prematch", label: "by reverse pre-match" },
  { value: "date", label: "by date" },
  { value: "closeness", label: "by closeness" },
];

export function SearchBar() {
  const query = useWorkspace((s) => s.search.query);
  const target = useWorkspace((s) => s.search.target);
  const sort = useWorkspace((s) => s.search.sort);
  const status = useWorkspace((s) => s.search.status);
  const history = useWorkspace((s) => s.searchHistory);

  const canSubmit = query.trim().length > 0 && target === "fulltext";

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    void workspace.runSearch();
  };

  return (
    <form className="mb-search" onSubmit={onSubmit} role="search">
      <input
        type="text"
        className="mb-search-input"
        placeholder="Search…"
        value={query}
        onChange={(e) => workspace.setSearchQuery(e.target.value)}
        aria-label="Search query"
        list="bkk-search-history"
      />
      <datalist id="bkk-search-history">
        {history.map((entry) => (
          <option key={entry.id} value={entry.query} />
        ))}
      </datalist>
      <select
        className="mb-select"
        value={target}
        onChange={(e) => workspace.setSearchTarget(e.target.value as SearchTarget)}
        aria-label="Search target"
      >
        {TARGETS.map((t) => (
          <option key={t.value} value={t.value} disabled={t.v2} title={t.v2 ? "v2" : undefined}>
            {t.label}
            {t.v2 ? " (v2)" : ""}
          </option>
        ))}
      </select>
      <select
        className="mb-select"
        value={sort}
        onChange={(e) => workspace.setSearchSort(e.target.value as SearchSort)}
        aria-label="Sort order"
      >
        {SORTS.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>
      <button
        type="submit"
        className="mb-search-go"
        disabled={!canSubmit || status === "loading"}
        title={canSubmit ? "Search (Enter)" : "Enter a query"}
      >
        {status === "loading" ? "…" : "Go"}
      </button>
    </form>
  );
}
