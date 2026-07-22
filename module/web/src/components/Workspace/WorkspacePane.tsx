import { useEffect, useState } from "react";
import { getManifest } from "../../api/client";
import { krClass } from "../../lib/krClass";
import { setResizing, useWorkspace, workspace, type PaneLeaf } from "../../state/useWorkspace";
import { CharInfoBar } from "../CharInfoBar";
import { Welcome } from "../Welcome";
import { CoreRecord } from "./CoreRecord";
import { DuplicationViewer } from "./DuplicationViewer";
import { ImagePanel } from "./ImagePanel";
import { TextViewer } from "./TextViewer";
import { TranslationSidecar } from "./TranslationSidecar";
import { BundleEditor } from "./BundleEditor";
import type { EditorPosition } from "../../lib/editorText";

function InspectResizer() {
  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault();
    window.getSelection()?.removeAllRanges();
    setResizing(true);
    const startX = e.clientX;
    const startWidth = workspace.state.panelWidths.inspect;
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      workspace.setPanelWidth("inspect", startWidth - dx);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
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

export function WorkspacePane({ pane, closeable = false }: { pane: PaneLeaf; closeable?: boolean }) {
  const defaultReadMode = useWorkspace((s) => s.readMode);
  const defaultLineMode = useWorkspace((s) => s.readPrefs.lineMode);
  const inspectWidth = useWorkspace((s) => s.panelWidths.inspect);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [seqsMap, setSeqsMap] = useState<Record<string, number[]>>({});
  const [editCursor, setEditCursor] = useState<EditorPosition>({
    offset: 0,
    ch: null,
    cp: null,
  });
  const activeTab =
    pane.tabs.find((t) => t.id === pane.activeTabId) ?? pane.tabs[0] ?? null;
  const activeTextTab = activeTab?.type === "text" ? activeTab : null;
  const activeCoreTab = activeTab?.type === "core-record" ? activeTab : null;
  const activeDupTab = activeTab?.type === "duplication" ? activeTab : null;

  const readMode = activeTextTab?.readMode ?? defaultReadMode;
  const lineMode = activeTextTab?.lineMode ?? defaultLineMode;
  const showEdit = readMode === "edit" && activeTextTab != null;
  const showTranslation =
    activeTextTab != null &&
    !showEdit &&
    (activeTextTab.showTranslation === true || readMode === "trans");
  const showImage =
    activeTextTab != null &&
    !showEdit &&
    (activeTextTab.showImage === true || readMode === "inspect");
  const selectedTranslation = activeTextTab?.selectedTranslation ?? null;
  const effectiveLineMode = showTranslation ? "phrase" : lineMode;

  useEffect(() => {
    let cancelled = false;
    const textTextids = pane.tabs
      .filter((t): t is typeof t & { type: "text" } => t.type === "text")
      .map((t) => t.textid);
    const missing = [...new Set(textTextids)].filter(
      (textid) => titles[textid] == null,
    );
    if (missing.length === 0) return;
    Promise.all(
      missing.map((textid) =>
        getManifest(textid)
          .then((m) => ({
            textid,
            title: m.metadata?.title ?? textid,
            seqs: (m.assets?.parts ?? []).map((p) => p.seq).sort((a, b) => a - b),
          }))
          .catch(() => ({ textid, title: textid, seqs: [] as number[] })),
      ),
    ).then((entries) => {
      if (cancelled) return;
      setTitles((prev) => {
        const next = { ...prev };
        for (const { textid, title } of entries) next[textid] = title;
        return next;
      });
      setSeqsMap((prev) => {
        const next = { ...prev };
        for (const { textid, seqs } of entries) next[textid] = seqs;
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [pane.tabs, titles]);

  return (
    <div className="wp" onMouseDown={() => workspace.focusPane(pane.id)}>
      <div className="tab-bar">
        {pane.tabs.length === 0 && (
          <div
            style={{
              padding: "0 12px",
              color: "var(--t3)",
              alignSelf: "center",
              fontSize: 11,
            }}
          >
            (no open text)
          </div>
        )}
        {pane.tabs.map((t) => {
          const isActive = t.id === activeTab?.id;
          if (t.type === "duplication") {
            return (
              <button
                key={t.id}
                className={`tab${isActive ? " on" : ""}`}
                title={`duplication row #${t.rowId}`}
                onClick={() => workspace.activateTab(pane.id, t.id)}
              >
                <span className="tab-title">dup #{t.rowId}</span>
                <span
                  className={`tab-pin${t.pinned ? " on" : ""}`}
                  role="button"
                  tabIndex={0}
                  title={t.pinned ? "Unpin row" : "Pin row"}
                  onClick={(e) => {
                    e.stopPropagation();
                    workspace.togglePinnedTab(pane.id, t.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter" && e.key !== " ") return;
                    e.preventDefault();
                    e.stopPropagation();
                    workspace.togglePinnedTab(pane.id, t.id);
                  }}
                >
                  {t.pinned ? "●" : "○"}
                </span>
              </button>
            );
          }
          if (t.type === "core-record") {
            return (
              <button
                key={t.id}
                className={`tab${isActive ? " on" : ""}`}
                title={`${t.collection}/${t.uuid}`}
                onClick={() => workspace.activateTab(pane.id, t.id)}
              >
                <span className="tab-title">
                  {t.collection} · {t.uuid.slice(0, 8)}
                </span>
                <span
                  className={`tab-pin${t.pinned ? " on" : ""}`}
                  role="button"
                  tabIndex={0}
                  title={t.pinned ? "Unpin record" : "Pin record"}
                  onClick={(e) => {
                    e.stopPropagation();
                    workspace.togglePinnedTab(pane.id, t.id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter" && e.key !== " ") return;
                    e.preventDefault();
                    e.stopPropagation();
                    workspace.togglePinnedTab(pane.id, t.id);
                  }}
                >
                  {t.pinned ? "●" : "○"}
                </span>
              </button>
            );
          }
          const seqs = isActive ? (seqsMap[t.textid] ?? []) : [];
          const curIdx = seqs.indexOf(t.seq);
          const prevSeq = curIdx > 0 ? seqs[curIdx - 1] : null;
          const nextSeq = curIdx >= 0 && curIdx < seqs.length - 1 ? seqs[curIdx + 1] : null;
          return (
            <button
              key={t.id}
              className={`tab${isActive ? " on" : ""}`}
              title={`${titles[t.textid] ?? t.textid} · ${t.textid} · 卷 ${t.seq}`}
              onClick={() => workspace.activateTab(pane.id, t.id)}
            >
              {isActive && (prevSeq !== null || nextSeq !== null) && (
                <span
                  className={`tab-juan-nav${prevSeq === null ? " disabled" : ""}`}
                  role="button"
                  tabIndex={prevSeq !== null ? 0 : -1}
                  title={prevSeq !== null ? `← 卷 ${prevSeq}` : undefined}
                  onClick={(e) => {
                    if (prevSeq === null) return;
                    e.stopPropagation();
                    workspace.openJuan(t.textid, prevSeq);
                  }}
                >←</span>
              )}
              <span className="tab-title">
                {titles[t.textid] ?? t.textid} · <span className={krClass(t.textid)}>{t.textid}</span> · 卷 {t.seq}
              </span>
              <span
                className={`tab-pin${t.pinned ? " on" : ""}`}
                role="button"
                tabIndex={0}
                title={t.pinned ? "Unpin text" : "Pin text"}
                onClick={(e) => {
                  e.stopPropagation();
                  workspace.togglePinnedTab(pane.id, t.id);
                }}
                onKeyDown={(e) => {
                  if (e.key !== "Enter" && e.key !== " ") return;
                  e.preventDefault();
                  e.stopPropagation();
                  workspace.togglePinnedTab(pane.id, t.id);
                }}
              >
                {t.pinned ? "●" : "○"}
              </span>
              {isActive && (prevSeq !== null || nextSeq !== null) && (
                <span
                  className={`tab-juan-nav${nextSeq === null ? " disabled" : ""}`}
                  role="button"
                  tabIndex={nextSeq !== null ? 0 : -1}
                  title={nextSeq !== null ? `卷 ${nextSeq} →` : undefined}
                  onClick={(e) => {
                    if (nextSeq === null) return;
                    e.stopPropagation();
                    workspace.openJuan(t.textid, nextSeq);
                  }}
                >→</span>
              )}
            </button>
          );
        })}
        {closeable && (
          <button
            className="pane-close"
            title="Close pane"
            onClick={(e) => {
              e.stopPropagation();
              workspace.closePane(pane.id);
            }}
          >
            ×
          </button>
        )}
      </div>
      {activeDupTab ? (
        <DuplicationViewer key={`dup:${activeDupTab.rowId}`} rowId={activeDupTab.rowId} />
      ) : activeCoreTab ? (
        <CoreRecord
          key={`${activeCoreTab.collection}:${activeCoreTab.uuid}`}
          paneId={pane.id}
          tabId={activeCoreTab.id}
          collection={activeCoreTab.collection}
          uuid={activeCoreTab.uuid}
        />
      ) : activeTextTab ? (
        showEdit ? (
          <BundleEditor
            key={`${activeTextTab.textid}:${activeTextTab.seq}`}
            textid={activeTextTab.textid}
            seq={activeTextTab.seq}
            editTarget={activeTextTab.editTarget ?? null}
            onCursorInfoChange={setEditCursor}
          />
        ) : (
          <div className={`ws-unified${showTranslation || showImage ? " has-sidecars" : ""}`}>
            <div className="ws-primary">
              <TextViewer
                key={`${activeTextTab.textid}:${activeTextTab.seq}`}
                paneId={pane.id}
                tabId={activeTextTab.id}
                textid={activeTextTab.textid}
                seq={activeTextTab.seq}
                lineMode={effectiveLineMode}
                translationAlign={showTranslation}
              />
            </div>
            {(showTranslation || showImage) && (
              <>
                <InspectResizer />
                <div className="ws-sidecars" style={{ width: inspectWidth }}>
                  {showTranslation && (
                    <TranslationSidecar
                      key={`trans:${activeTextTab.textid}:${activeTextTab.seq}:${selectedTranslation?.id ?? ""}`}
                      paneId={pane.id}
                      tabId={activeTextTab.id}
                      textid={activeTextTab.textid}
                      seq={activeTextTab.seq}
                      translationId={
                        selectedTranslation?.source_textid === activeTextTab.textid
                          ? selectedTranslation.id
                          : null
                      }
                    />
                  )}
                  {showImage && (
                    <ImagePanel
                      key={`image:${activeTextTab.textid}:${activeTextTab.seq}`}
                      textid={activeTextTab.textid}
                      seq={activeTextTab.seq}
                      paneId={pane.id}
                      tabId={activeTextTab.id}
                    />
                  )}
                </div>
              </>
            )}
          </div>
        )
      ) : (
        <Welcome empty="Select a text from the catalog or TOC." />
      )}
      {!activeCoreTab && !activeDupTab && (
        <CharInfoBar
          ch={showEdit ? editCursor.ch : activeTextTab?.hoverChar ?? null}
          cp={showEdit ? editCursor.cp : activeTextTab?.hoverCodepoint ?? null}
          offset={showEdit ? editCursor.offset : null}
          bucket={showEdit ? null : activeTextTab?.hoverBucket ?? null}
          hoverOffset={showEdit ? null : activeTextTab?.hoverOffset ?? null}
          bucketLength={showEdit ? null : activeTextTab?.hoverBucketLength ?? null}
          onJumpToOffset={
            showEdit || activeTextTab == null
              ? undefined
              : (bucket, offset) =>
                  workspace.highlightTextLocation({
                    textid: activeTextTab.textid,
                    seq: activeTextTab.seq,
                    bucket,
                    offset,
                    length: 1,
                  })
          }
        />
      )}
    </div>
  );
}
