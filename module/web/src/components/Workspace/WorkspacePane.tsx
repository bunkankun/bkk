import { useEffect, useState } from "react";
import { getManifest } from "../../api/client";
import { krClass } from "../../lib/krClass";
import { setResizing, useWorkspace, workspace, type PaneLeaf } from "../../state/useWorkspace";
import { CharInfoBar } from "../CharInfoBar";
import { ImagePanel } from "./ImagePanel";
import { TextViewer } from "./TextViewer";
import { TranslationViewer } from "./TranslationViewer";

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
  const selectedTranslation = useWorkspace((s) => s.selectedTranslation);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [seqsMap, setSeqsMap] = useState<Record<string, number[]>>({});
  const activeTab =
    pane.tabs.find((t) => t.id === pane.activeTabId) ?? pane.tabs[0] ?? null;

  const readMode = activeTab?.readMode ?? defaultReadMode;
  const lineMode = activeTab?.lineMode ?? defaultLineMode;
  const showInspect = readMode === "inspect" && activeTab != null;
  const showTranslation = readMode === "trans" && activeTab != null;

  useEffect(() => {
    let cancelled = false;
    const missing = [...new Set(pane.tabs.map((t) => t.textid))].filter(
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
          const seqs = isActive ? (seqsMap[t.textid] ?? []) : [];
          const curIdx = seqs.indexOf(t.seq);
          const prevSeq = curIdx > 0 ? seqs[curIdx - 1] : null;
          const nextSeq = curIdx >= 0 && curIdx < seqs.length - 1 ? seqs[curIdx + 1] : null;
          return (
            <button
              key={t.id}
              className={`tab${isActive ? " on" : ""}`}
              title={`${titles[t.textid] ?? t.textid} · ${t.textid} · 卷 ${t.seq}`}
              onClick={() => workspace.focusPane(pane.id)}
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
      {activeTab ? (
        showInspect ? (
          <div className="ws-split">
            <div className="ws-split-left">
              <TextViewer
                key={`${activeTab.textid}:${activeTab.seq}`}
                paneId={pane.id}
                tabId={activeTab.id}
                textid={activeTab.textid}
                seq={activeTab.seq}
                lineMode={lineMode}
              />
            </div>
            <InspectResizer />
            <div className="ws-split-right" style={{ width: inspectWidth }}>
              <ImagePanel
                key={`${activeTab.textid}:${activeTab.seq}`}
                textid={activeTab.textid}
                seq={activeTab.seq}
              />
            </div>
          </div>
        ) : showTranslation ? (
          <TranslationViewer
            key={`${activeTab.textid}:${activeTab.seq}:${selectedTranslation?.id ?? ""}`}
            paneId={pane.id}
            tabId={activeTab.id}
            textid={activeTab.textid}
            seq={activeTab.seq}
            translationId={
              selectedTranslation?.source_textid === activeTab.textid
                ? selectedTranslation.id
                : null
            }
          />
        ) : (
          <TextViewer
            key={`${activeTab.textid}:${activeTab.seq}`}
            paneId={pane.id}
            tabId={activeTab.id}
            textid={activeTab.textid}
            seq={activeTab.seq}
            lineMode={lineMode}
          />
        )
      ) : (
        <div className="empty-pane">Select a text from the catalog or TOC.</div>
      )}
      <CharInfoBar ch={activeTab?.hoverChar ?? null} cp={activeTab?.hoverCodepoint ?? null} />
    </div>
  );
}
