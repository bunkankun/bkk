import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ApiError,
  getCoreBacklinks,
  getCoreConceptWords,
  getCoreRecord,
  getCoreSuperEntryByOrth,
  openCoreRecordPr,
  patchCoreRecord,
} from "../../api/client";
import type {
  CoreBacklinksResponse,
  CoreConceptWord,
  CoreRecordResponse,
} from "../../api/types";
import { findTab, useWorkspace, workspace } from "../../state/useWorkspace";

const LOCKED_FRONTMATTER_KEYS = new Set(["uuid", "type"]);

interface EditDraft {
  branch: string;
  parent_sha: string;
  commit_sha: string;
  fork_repo: string;
  compare_url: string;
  pr_url: string | null;
  frontmatter: Record<string, unknown>;
  body_markdown: string;
}

const KNOWN_COLLECTIONS = new Set([
  "concepts",
  "graphs",
  "syntactic-functions",
  "semantic-features",
  "bibliography",
  "words",
  "super-entries",
]);

// Canonical UUID shape (8-4-4-4-12 hex). The `.md` suffix is optional because
// some source records have a stray space in the href, which markdown parsers
// truncate at — we still want to follow those links.
const UUID_RE = String.raw`[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}`;

// Cross-collection link: `.../<collection>/<hex>/[uuid-]<uuid>[.md]`
const CROSS_COLLECTION_RE = new RegExp(
  String.raw`(?:^|/)(concepts|graphs|syntactic-functions|semantic-features|bibliography|words|super-entries)/` +
    String.raw`[0-9a-f]+/(?:uuid-)?(${UUID_RE})(?:\.md)?`,
);

// Same-collection sibling link: `../<hex>/[uuid-]<uuid>[.md]` (no collection name in path).
const SAME_COLLECTION_RE = new RegExp(
  String.raw`(?:^|/)[0-9a-f]+/(?:uuid-)?(${UUID_RE})(?:\.md)?(?:[#?].*)?$`,
);

const WIKILINK_SCHEME = "bkk-wikilink:";

function parseCoreHref(
  href: string,
  currentCollection: string,
): { collection: string; uuid: string } | null {
  if (!href) return null;
  const cross = CROSS_COLLECTION_RE.exec(href);
  if (cross && KNOWN_COLLECTIONS.has(cross[1])) {
    return { collection: cross[1], uuid: cross[2] };
  }
  const same = SAME_COLLECTION_RE.exec(href);
  if (same) {
    return { collection: currentCollection, uuid: same[1] };
  }
  return null;
}

// react-markdown's default urlTransform rejects unknown schemes (so our
// `bkk-wikilink:` href is silently stripped) and percent-encodes the path,
// which can confuse our relative-link regex. Let everything through — we
// handle dispatch ourselves in the `a` component.
function passthroughUrlTransform(href: string): string {
  return href;
}

// Convert `[[X]]` (CJK) wikilinks into markdown anchors with a sentinel scheme
// so ReactMarkdown's link handler can intercept them. Skip occurrences inside
// fenced code blocks.
function preprocessWikilinks(body: string): string {
  const lines = body.split("\n");
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    lines[i] = line.replace(
      /\[\[([^\]\n|]+)\]\]/g,
      (_, inner: string) => {
        const orth = inner.trim();
        if (!orth) return _;
        return `[${orth}](${WIKILINK_SCHEME}${encodeURIComponent(orth)})`;
      },
    );
  }
  return lines.join("\n");
}

type State =
  | { status: "loading" }
  | { status: "ok"; record: CoreRecordResponse }
  | { status: "error"; error: string; status_code?: number };

export function CoreRecord({
  paneId,
  tabId,
  collection,
  uuid,
}: {
  paneId: string;
  tabId: string;
  collection: string;
  uuid: string;
}) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [showFrontmatter, setShowFrontmatter] = useState(false);
  const [conceptWords, setConceptWords] = useState<CoreConceptWord[] | null>(null);
  const [backlinks, setBacklinks] = useState<CoreBacklinksResponse | null>(null);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [editing, setEditing] = useState(false);
  const [editFm, setEditFm] = useState<Array<[string, string]>>([]);
  const [editBody, setEditBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [editConflict, setEditConflict] = useState(false);
  const [openingPr, setOpeningPr] = useState(false);
  const authStatus = useWorkspace((s) => s.auth.status);
  const historyLen = useWorkspace((s) => {
    const tab = findTab(s.pane, paneId, tabId);
    return tab?.type === "core-record" ? tab.history?.length ?? 0 : 0;
  });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    setDraft(null);
    setEditing(false);
    setEditError(null);
    setEditConflict(false);
    getCoreRecord(collection, uuid)
      .then((record) => {
        if (!cancelled) setState({ status: "ok", record });
      })
      .catch((e) => {
        if (!cancelled) setState({ status: "error", error: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [collection, uuid]);

  useEffect(() => {
    setBacklinks(null);
    if (collection === "concepts" || collection === "words") {
      // "Referenced by" is noisy for these collections (every word references
      // its concept, every concept references the same canonical refs).
      return;
    }
    let cancelled = false;
    getCoreBacklinks(collection, uuid)
      .then((r) => {
        if (!cancelled) setBacklinks(r);
      })
      .catch(() => {
        if (!cancelled) setBacklinks({ uuid, total: 0, groups: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [collection, uuid]);

  useEffect(() => {
    if (collection !== "concepts") {
      setConceptWords(null);
      return;
    }
    let cancelled = false;
    setConceptWords(null);
    getCoreConceptWords(uuid)
      .then((r) => {
        if (!cancelled) setConceptWords(r.words);
      })
      .catch(() => {
        if (!cancelled) setConceptWords([]);
      });
    return () => {
      cancelled = true;
    };
  }, [collection, uuid]);

  const displayFrontmatter = useMemo(() => {
    if (draft) return draft.frontmatter;
    return state.status === "ok" ? state.record.frontmatter : {};
  }, [state, draft]);

  const displayBody = useMemo(() => {
    if (draft) return draft.body_markdown;
    return state.status === "ok" ? state.record.body_markdown : "";
  }, [state, draft]);

  const frontmatterText = useMemo(() => {
    try {
      return JSON.stringify(displayFrontmatter, null, 2);
    } catch {
      return "";
    }
  }, [displayFrontmatter]);

  const processedBody = useMemo(() => {
    return preprocessWikilinks(displayBody);
  }, [displayBody]);

  if (state.status === "loading") {
    return <div className="empty-pane">Loading core record…</div>;
  }
  if (state.status === "error") {
    return <div className="empty-pane">Failed to load record: {state.error}</div>;
  }

  const record = state.record;

  const replaceWithCore = (nextCollection: string, nextUuid: string) => {
    workspace.replaceCoreRecord(paneId, tabId, nextCollection, nextUuid);
  };

  const startEdit = () => {
    const fm = displayFrontmatter;
    const entries: Array<[string, string]> = [];
    for (const [k, v] of Object.entries(fm)) {
      if (LOCKED_FRONTMATTER_KEYS.has(k)) continue;
      let s: string;
      if (typeof v === "string") s = v;
      else if (v == null) s = "";
      else s = JSON.stringify(v);
      entries.push([k, s]);
    }
    setEditFm(entries);
    setEditBody(displayBody);
    setEditError(null);
    setEditConflict(false);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditError(null);
  };

  const saveEdit = async () => {
    if (state.status !== "ok") return;
    const fm: Record<string, unknown> = {};
    // Preserve locked fields from the original record so the PATCH includes
    // every existing key.
    for (const k of ["uuid", "type"]) {
      const v = (displayFrontmatter as Record<string, unknown>)[k];
      if (v !== undefined) fm[k] = v;
    }
    for (const [k, raw] of editFm) {
      const original = (displayFrontmatter as Record<string, unknown>)[k];
      if (typeof original === "string" || original == null) {
        fm[k] = raw;
      } else {
        try {
          fm[k] = JSON.parse(raw);
        } catch (e) {
          setEditError(`field ${k}: invalid JSON (${String(e)})`);
          return;
        }
      }
    }
    setSaving(true);
    setEditError(null);
    setEditConflict(false);
    try {
      const resp = await patchCoreRecord(collection, uuid, {
        frontmatter: fm,
        body: editBody,
        parent_sha: draft?.parent_sha,
        branch: draft?.branch,
      });
      setDraft({
        branch: resp.branch,
        parent_sha: resp.parent_sha,
        commit_sha: resp.commit_sha,
        fork_repo: resp.fork_repo,
        compare_url: resp.compare_url,
        pr_url: resp.pr_url,
        frontmatter: resp.frontmatter,
        body_markdown: resp.body_markdown,
      });
      setEditing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setEditConflict(true);
        setEditError(
          "This record changed upstream since you started editing. Discard your draft and reload to start over.",
        );
      } else {
        setEditError(String(e));
      }
    } finally {
      setSaving(false);
    }
  };

  const discardDraft = () => {
    setDraft(null);
    setEditing(false);
    setEditError(null);
    setEditConflict(false);
    // re-fetch upstream
    setState({ status: "loading" });
    getCoreRecord(collection, uuid)
      .then((record) => setState({ status: "ok", record }))
      .catch((e) => setState({ status: "error", error: String(e) }));
  };

  const openPr = async () => {
    if (!draft) return;
    setOpeningPr(true);
    setEditError(null);
    try {
      const resp = await openCoreRecordPr(collection, uuid, {
        branch: draft.branch,
      });
      setDraft({ ...draft, pr_url: resp.pr_url });
    } catch (e) {
      setEditError(String(e));
    } finally {
      setOpeningPr(false);
    }
  };

  const resolveWikilink = (orth: string) => {
    getCoreSuperEntryByOrth(orth)
      .then((r) => replaceWithCore("super-entries", r.uuid))
      .catch(() => {
        // No super-entry exists for this orth; do nothing for now.
      });
  };

  const handleLinkClick = (href: string | undefined, e: React.MouseEvent) => {
    if (!href) return;
    if (href.startsWith(WIKILINK_SCHEME)) {
      e.preventDefault();
      const orth = decodeURIComponent(href.slice(WIKILINK_SCHEME.length));
      resolveWikilink(orth);
      return;
    }
    const parsed = parseCoreHref(href, record.collection);
    if (!parsed) return;
    e.preventDefault();
    replaceWithCore(parsed.collection, parsed.uuid);
  };

  const showRelated =
    record.collection !== "words" &&
    record.collection !== "concepts" &&
    record.links.length > 0;

  return (
    <div
      className="core-record"
      style={{
        overflow: "auto",
        padding: "12px 16px",
        userSelect: "text",
        WebkitUserSelect: "text",
        cursor: "text",
      }}
      onMouseUp={() => {
        const sel = window.getSelection();
        if (!sel || sel.isCollapsed) return;
        const text = sel.toString().trim();
        if (text) workspace.setSearchQuery(text);
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <button
          type="button"
          onClick={() => workspace.coreRecordBack(paneId, tabId)}
          disabled={historyLen === 0}
          title={historyLen === 0 ? "No previous record" : "Back"}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "var(--bg-1)",
            color: historyLen === 0 ? "var(--t3)" : "var(--t1)",
            border: "1px solid var(--bd)",
            borderRadius: 3,
            cursor: historyLen === 0 ? "default" : "pointer",
          }}
        >
          ← Back
        </button>
        <h2 style={{ fontSize: 16, margin: 0, color: "var(--t1)" }}>
          {record.display_label}
        </h2>
        {authStatus === "authenticated" && !editing && (
          <button
            type="button"
            onClick={startEdit}
            title="Edit this record on your GitHub fork"
            style={{
              fontSize: 11,
              padding: "2px 8px",
              background: "var(--bg-1)",
              color: "var(--t1)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
              cursor: "pointer",
              marginLeft: "auto",
            }}
          >
            Edit
          </button>
        )}
      </div>
      <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 12 }}>
        {record.collection} · {record.uuid}
      </div>

      {draft && (
        <div
          style={{
            fontSize: 11,
            padding: "6px 10px",
            marginBottom: 12,
            background: "var(--bg-1)",
            border: "1px solid var(--bd)",
            borderRadius: 3,
            color: "var(--t1)",
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <span>
            Viewing your unmerged edit on{" "}
            <a
              href={draft.compare_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--link)" }}
            >
              {draft.branch}
            </a>{" "}
            (commit <code>{draft.commit_sha.slice(0, 7)}</code>).
          </span>
          {draft.pr_url ? (
            <a
              href={draft.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--link)" }}
            >
              View PR ↗
            </a>
          ) : (
            <button
              type="button"
              onClick={openPr}
              disabled={openingPr}
              style={{
                fontSize: 11,
                padding: "1px 6px",
                background: "var(--bg-2)",
                color: "var(--t1)",
                border: "1px solid var(--bd)",
                borderRadius: 3,
                cursor: openingPr ? "default" : "pointer",
              }}
            >
              {openingPr ? "Opening…" : "Open PR"}
            </button>
          )}
          <button
            type="button"
            onClick={discardDraft}
            title="Discard local draft and reload upstream"
            style={{
              fontSize: 11,
              padding: "1px 6px",
              background: "var(--bg-2)",
              color: "var(--t1)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
              cursor: "pointer",
            }}
          >
            Reset to upstream
          </button>
        </div>
      )}

      <details
        open={showFrontmatter}
        onToggle={(e) => setShowFrontmatter((e.target as HTMLDetailsElement).open)}
        style={{ marginBottom: 12 }}
      >
        <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--t2)" }}>
          Frontmatter
        </summary>
        <pre
          style={{
            fontSize: 11,
            background: "var(--bg-1)",
            padding: 8,
            border: "1px solid var(--bd)",
            borderRadius: 3,
            overflow: "auto",
            color: "var(--t1)",
            maxHeight: 320,
          }}
        >
          {frontmatterText}
        </pre>
      </details>

      {showRelated && (
        <div style={{ marginBottom: 12, fontSize: 12 }}>
          <div style={{ color: "var(--t2)", marginBottom: 4 }}>Related</div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {record.links.map((l, i) => (
              <li key={`${l.target_uuid}-${i}`}>
                {l.target_collection ? (
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      replaceWithCore(l.target_collection!, l.target_uuid);
                    }}
                    style={{ color: "var(--link)" }}
                  >
                    {l.target_label ?? l.target_uuid}
                  </a>
                ) : (
                  <span>{l.target_label ?? l.target_uuid}</span>
                )}
                {l.relation && (
                  <span style={{ color: "var(--t3)" }}> — {l.relation}</span>
                )}
                {l.target_type && (
                  <span style={{ color: "var(--t3)" }}> ({l.target_type})</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {backlinks &&
        backlinks.total > 0 &&
        record.collection !== "concepts" &&
        record.collection !== "words" && (
          <BacklinksSection backlinks={backlinks} onOpen={replaceWithCore} />
        )}

      {editing && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            saveEdit();
          }}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            marginBottom: 16,
            padding: 10,
            border: "1px solid var(--bd)",
            borderRadius: 3,
            background: "var(--bg-1)",
          }}
        >
          <div style={{ fontSize: 11, color: "var(--t2)" }}>Frontmatter</div>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 8px" }}>
            {["uuid", "type"].map((k) => {
              const v = (displayFrontmatter as Record<string, unknown>)[k];
              if (v === undefined) return null;
              return (
                <div key={k} style={{ display: "contents" }}>
                  <label style={{ fontSize: 11, color: "var(--t3)" }}>{k}</label>
                  <code style={{ fontSize: 11, color: "var(--t3)" }}>{String(v)}</code>
                </div>
              );
            })}
            {editFm.map(([k, v], idx) => (
              <div key={k} style={{ display: "contents" }}>
                <label style={{ fontSize: 11, color: "var(--t2)" }} htmlFor={`fm-${k}`}>
                  {k}
                </label>
                <input
                  id={`fm-${k}`}
                  type="text"
                  value={v}
                  onChange={(e) => {
                    const next = editFm.slice();
                    next[idx] = [k, e.target.value];
                    setEditFm(next);
                  }}
                  style={{
                    fontSize: 12,
                    padding: "2px 6px",
                    background: "var(--bg-0)",
                    color: "var(--t1)",
                    border: "1px solid var(--bd)",
                    borderRadius: 3,
                  }}
                />
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: "var(--t2)" }}>Body</div>
          <textarea
            value={editBody}
            onChange={(e) => setEditBody(e.target.value)}
            rows={20}
            style={{
              fontFamily: "monospace",
              fontSize: 12,
              padding: 6,
              background: "var(--bg-0)",
              color: "var(--t1)",
              border: "1px solid var(--bd)",
              borderRadius: 3,
              resize: "vertical",
            }}
          />
          {editError && (
            <div style={{ fontSize: 11, color: "var(--t1)", background: "var(--err)", padding: "4px 8px", borderRadius: 3 }}>
              {editError}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            {editConflict && (
              <button
                type="button"
                onClick={discardDraft}
                style={{
                  fontSize: 11,
                  padding: "3px 10px",
                  background: "var(--bg-2)",
                  color: "var(--t1)",
                  border: "1px solid var(--bd)",
                  borderRadius: 3,
                  cursor: "pointer",
                }}
              >
                Reload from upstream
              </button>
            )}
            <button
              type="button"
              onClick={cancelEdit}
              style={{
                fontSize: 11,
                padding: "3px 10px",
                background: "var(--bg-2)",
                color: "var(--t1)",
                border: "1px solid var(--bd)",
                borderRadius: 3,
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              style={{
                fontSize: 11,
                padding: "3px 10px",
                background: "var(--bg-2)",
                color: "var(--t1)",
                border: "1px solid var(--bd)",
                borderRadius: 3,
                cursor: saving ? "default" : "pointer",
              }}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      )}

      <div className="core-record-body" style={{ fontSize: 13, lineHeight: 1.55 }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          urlTransform={passthroughUrlTransform}
          components={{
            a: ({ href, children, ...rest }) => {
              const isWikilink = href?.startsWith(WIKILINK_SCHEME);
              const parsed =
                !isWikilink && href ? parseCoreHref(href, record.collection) : null;
              if (isWikilink || parsed) {
                return (
                  <a
                    href={href}
                    onClick={(e) => handleLinkClick(href, e)}
                    style={{ color: "var(--link)" }}
                    {...rest}
                  >
                    {children}
                  </a>
                );
              }
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--link)" }}
                  {...rest}
                >
                  {children}
                </a>
              );
            },
          }}
        >
          {processedBody}
        </ReactMarkdown>

        {collection === "concepts" && conceptWords != null && conceptWords.length > 0 && (
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            {conceptWords.map((w) => (
              <li key={w.uuid}>
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    replaceWithCore("words", w.uuid);
                  }}
                  style={{ color: "var(--link)" }}
                >
                  {w.super_entry_orth ?? w.display_label ?? w.uuid}
                </a>
                {w.n != null && (
                  <span style={{ color: "var(--t3)" }}> · n={w.n}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

const COLLECTION_TITLES: Record<string, string> = {
  concepts: "Concepts",
  graphs: "Graphs",
  "syntactic-functions": "Syntactic functions",
  "semantic-features": "Semantic features",
  bibliography: "Bibliography",
  words: "Words",
  "super-entries": "Super-entries",
};

function BacklinksSection({
  backlinks,
  onOpen,
}: {
  backlinks: CoreBacklinksResponse;
  onOpen: (collection: string, uuid: string) => void;
}) {
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  return (
    <div style={{ marginBottom: 12, fontSize: 12 }}>
      <div style={{ color: "var(--t2)", marginBottom: 4 }}>
        Referenced by ({backlinks.total})
      </div>
      {backlinks.groups.map((g) => {
        const isOpen = !!openGroups[g.collection];
        const label = COLLECTION_TITLES[g.collection] ?? g.collection;
        return (
          <div key={g.collection}>
            <div
              className="cat-sub"
              onClick={() =>
                setOpenGroups((p) => ({ ...p, [g.collection]: !p[g.collection] }))
              }
              title={label}
            >
              <span className="cat-caret">{isOpen ? "▾" : "▸"}</span>
              <span className="cat-zh">{label}</span>
              <span className="cat-count">{g.total}</span>
            </div>
            {isOpen && (
              <ul style={{ margin: "2px 0 6px 0", paddingLeft: 28 }}>
                {g.items.map((it) => (
                  <li key={it.uuid}>
                    <a
                      href="#"
                      onClick={(e) => {
                        e.preventDefault();
                        onOpen(it.collection, it.uuid);
                      }}
                      style={{ color: "var(--link)" }}
                    >
                      {it.display_label}
                    </a>
                    {it.relation && (
                      <span style={{ color: "var(--t3)" }}> — {it.relation}</span>
                    )}
                  </li>
                ))}
                {g.total > g.items.length && (
                  <li style={{ color: "var(--t3)" }}>
                    … {g.total - g.items.length} more
                  </li>
                )}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
