import type { SearchSort, TranslationSort } from "../../api/types";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import type { SearchTarget } from "../../state/useWorkspace";

const TARGETS: { value: SearchTarget; label: string; disabled: boolean }[] = [
  { value: "fulltext", label: "Full text", disabled: false },
  { value: "dictionary", label: "Dictionary", disabled: true },
  { value: "translations", label: "Translations", disabled: false },
];

const SORTS: { value: SearchSort; label: string }[] = [
  { value: "match", label: "by match" },
  { value: "textid", label: "by text id" },
  { value: "reverse_prematch", label: "by reverse pre-match" },
  { value: "date", label: "by date" },
  { value: "closeness", label: "by closeness" },
];

const TRANS_SORTS: { value: TranslationSort; label: string }[] = [
  { value: "textid", label: "by text id" },
  { value: "trans_date", label: "by translation date" },
  { value: "source_date", label: "by source date" },
];

export function SearchBar() {
  const query = useWorkspace((s) => s.search.query);
  const target = useWorkspace((s) => s.search.target);
  const sort = useWorkspace((s) => s.search.sort);
  const translationSort = useWorkspace((s) => s.search.translationSort);
  const status = useWorkspace((s) => s.search.status);
  const history = useWorkspace((s) => s.searchHistory);

  const canSubmit =
    query.trim().length > 0 && (target === "fulltext" || target === "translations");

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
          <option key={t.value} value={t.value} disabled={t.disabled}>
            {t.label}
          </option>
        ))}
      </select>
      {target === "translations" ? (
        <select
          className="mb-select"
          value={translationSort}
          onChange={(e) => workspace.setTranslationSort(e.target.value as TranslationSort)}
          aria-label="Sort order"
        >
          {TRANS_SORTS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      ) : (
        <select
          className="mb-select"
          value={sort}
          onChange={(e) => workspace.setSearchSort(e.target.value as SearchSort)}
          aria-label="Sort order"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      )}
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
