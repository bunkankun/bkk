import { useState } from "react";
import { useWorkspace, workspace } from "../../state/useWorkspace";
import { listPathFromName } from "../../lib/textLists";

export function Lists() {
  const lists = useWorkspace((s) => s.textLists);
  const active = useWorkspace((s) => s.activeListPaths);
  const activeTextid = useWorkspace((s) => s.activeTextid);
  const [selectedPath, setSelectedPath] = useState<string | null>(lists[0]?.path ?? null);
  const selected = lists.find((list) => list.path === selectedPath) ?? lists[0] ?? null;
  const [draft, setDraft] = useState<string | null>(null);
  const content = draft ?? selected?.content ?? "";

  const select = (path: string) => {
    setSelectedPath(path);
    setDraft(null);
  };
  const create = async () => {
    const name = window.prompt("List name", "New list");
    if (!name) return;
    await workspace.createTextList(name);
    setSelectedPath(listPathFromName(name));
    setDraft(null);
  };
  const rename = async () => {
    if (!selected) return;
    const name = window.prompt("Rename list", selected.name);
    if (!name) return;
    await workspace.renameTextList(selected.path, name);
    setSelectedPath(listPathFromName(name));
    setDraft(null);
  };
  const remove = async () => {
    if (!selected) return;
    if (!window.confirm(`Delete "${selected.name}"?`)) return;
    await workspace.deleteTextList(selected.path);
    setSelectedPath(null);
    setDraft(null);
  };
  const save = async () => {
    if (!selected) return;
    await workspace.saveTextList(selected.path, content);
    setDraft(null);
  };

  return (
    <div className="lists-panel">
      <div className="lists-toolbar">
        <button type="button" onClick={create}>New</button>
      </div>
      <div className="lists-list">
        {lists.length === 0 ? (
          <div className="empty">No lists yet.</div>
        ) : (
          lists.map((list) => (
            <button
              key={list.path}
              type="button"
              className={`text-list-row${selected?.path === list.path ? " on" : ""}`}
              onClick={() => select(list.path)}
            >
              <input
                type="checkbox"
                checked={active.includes(list.path)}
                onChange={(e) => workspace.setListActive(list.path, e.target.checked)}
                onClick={(e) => e.stopPropagation()}
                title="Mark this list in search results"
              />
              <span>{list.name}</span>
              <em>{list.textids.length}</em>
            </button>
          ))
        )}
      </div>
      {selected ? (
        <div className="list-editor">
          <div className="list-editor-head">
            <strong>{selected.name}</strong>
            <button type="button" onClick={rename}>Rename</button>
            <button type="button" onClick={remove}>Delete</button>
          </div>
          <textarea
            value={content}
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="list-editor-actions">
            <button type="button" disabled={draft == null} onClick={save}>Save</button>
            <button type="button" disabled={draft == null} onClick={() => setDraft(null)}>
              Revert
            </button>
            <button
              type="button"
              disabled={!activeTextid}
              onClick={() => workspace.addCurrentTextToList(selected.path)}
            >
              Add current
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
