"use client";

import { useMemo } from "react";
import type { Folder } from "../../lib/api";

/** Plain-text glyphs only. No third-party icon set, no brand marks. */
const FOLDER_GLYPH: Record<string, string> = {
  inbox: "▤",
  sentitems: "▷",
  drafts: "✎",
  archive: "▦",
  deleteditems: "▧",
  junkemail: "⊘",
  outbox: "▸",
};

export default function FolderPane({
  folders,
  selected,
  unreadTotal,
  syncState,
  lastSyncedAt,
  onSelect,
  onToggle,
  collapsed,
}: {
  folders: Folder[];
  selected: string | null;
  unreadTotal: number;
  syncState: "complete" | "indexing" | "unknown";
  lastSyncedAt: string | null;
  onSelect: (folderId: string) => void;
  onToggle: () => void;
  collapsed: boolean;
}) {
  const syncLine = useMemo(() => {
    if (syncState === "indexing") {
      return "Sync in progress: results may be incomplete";
    }
    if (!lastSyncedAt) return "Not synced yet";
    const stamp = new Date(lastSyncedAt);
    if (Number.isNaN(stamp.getTime())) return "Synced";
    return `Last synced ${stamp.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })}`;
  }, [syncState, lastSyncedAt]);

  return (
    <nav
      className="folder-pane"
      data-collapsed={collapsed ? "true" : "false"}
      aria-label="Mail folders"
    >
      <button
        type="button"
        className="folder-collapse"
        onClick={onToggle}
        aria-expanded={!collapsed}
        title={collapsed ? "Show folders" : "Hide folders"}
      >
        <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
        <span className="sr-only">{collapsed ? "Show folders" : "Hide folders"}</span>
      </button>

      {collapsed ? (
        <ul className="folder-list folder-list-collapsed">
          {folders.map((folder) => (
            <li key={folder.id}>
              <button
                type="button"
                className="folder-row"
                data-selected={folder.id === selected ? "true" : "false"}
                onClick={() => onSelect(folder.id)}
                title={folder.name}
                aria-label={`${folder.name}${folder.unread ? `, ${folder.unread} unread` : ""}`}
                aria-current={folder.id === selected ? "true" : undefined}
              >
                <span className="folder-glyph" aria-hidden="true">
                  {FOLDER_GLYPH[folder.id.toLowerCase()] ?? "▫"}
                </span>
                {folder.unread > 0 ? (
                  <span className="folder-badge" aria-hidden="true">
                    {folder.unread > 999 ? "999+" : folder.unread}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <>
          <h2 className="pane-title">Folders</h2>
          <ul className="folder-list">
            {folders.map((folder) => (
              <li key={folder.id}>
                <button
                  type="button"
                  className="folder-row"
                  data-selected={folder.id === selected ? "true" : "false"}
                  onClick={() => onSelect(folder.id)}
                  aria-current={folder.id === selected ? "true" : undefined}
                >
                  <span className="folder-glyph" aria-hidden="true">
                    {FOLDER_GLYPH[folder.id.toLowerCase()] ?? "▫"}
                  </span>
                  <span className="folder-name">{folder.name}</span>
                  {folder.unread > 0 ? (
                    <span className="folder-badge">{folder.unread > 999 ? "999+" : folder.unread}</span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
          <p className="folder-sync" data-state={syncState} role="status">
            <span className="sync-dot" aria-hidden="true" data-state={syncState} />
            <span>
              {syncLine}
              {unreadTotal > 0 ? ` · ${unreadTotal} unread` : ""}
            </span>
          </p>
        </>
      )}
    </nav>
  );
}