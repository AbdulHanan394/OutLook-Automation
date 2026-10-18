"use client";

import { useMemo } from "react";
import type { Folder } from "../../lib/api";

/**
 * Plain-text glyphs only. No third-party icon set, no brand marks.
 *
 * Every folder gets a *distinct* glyph: the well-known Outlook keys have one
 * each, and a custom folder falls back to the initial of its display name so two
 * of them never render as the same square. At 20px in the collapsed rail a
 * shared glyph is the only thing distinguishing one folder from another.
 */
const FOLDER_GLYPH: Record<string, string> = {
  inbox: "▤",
  sentitems: "➤",
  drafts: "✎",
  archive: "▦",
  deleteditems: "▧",
  junkemail: "⊘",
  outbox: "⇥",
  recoverableitemsdeletions: "↺",
};

function glyphFor(folder: Folder): string {
  const known = FOLDER_GLYPH[folder.id.toLowerCase()];
  if (known) return known;
  const initial = folder.name.trim().charAt(0);
  return initial ? initial.toUpperCase() : "▫";
}

/**
 * Over 99 the badge is drawn as a dot -- four digits do not fit beside a 20px
 * glyph in a 56px rail -- but it still *reads* as a number, because the count
 * is the thing the reader is looking for and a bare dot would hide it from
 * anything reading the text.
 */
function badgeLabel(unread: number): { text: string; dot: boolean } | null {
  if (unread <= 0) return null;
  if (unread > 99) return { text: "999+", dot: true };
  return { text: unread > 9 ? "9+" : String(unread), dot: false };
}

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

  // A configured folder with nothing in it is noise in the rail, where there is
  // no room for a name to explain it. The sidebar keeps showing it; this list is
  // a launcher, and a launcher should not offer dead ends.
  const listed = useMemo(() => folders.filter((folder) => folder.total > 0), [folders]);
  const toggleLabel = collapsed ? "Expand folders" : "Collapse folders";

  return (
    <nav
      className="folder-pane"
      data-collapsed={collapsed ? "true" : "false"}
      aria-label="Mail folders"
    >
      <div className="folder-pane-head">
        <h2 className="pane-title">Folders</h2>
        {/* Always visible, never inside the scrolling list: a control you have to
            scroll to find is not a control. */}
        <button
          type="button"
          className="folder-collapse"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-label={toggleLabel}
          title={toggleLabel}
        >
          <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
        </button>
      </div>

      <ul className="folder-list">
        {listed.map((folder) => {
          const badge = badgeLabel(folder.unread);
          const unreadText = folder.unread > 0 ? `, ${folder.unread} unread` : "";
          return (
            <li key={folder.id}>
              <button
                type="button"
                className="folder-row"
                data-selected={folder.id === selected ? "true" : "false"}
                onClick={() => onSelect(folder.id)}
                // The rail has no room for the name, so it is carried by the
                // tooltip, the accessible name and the browser title.
                title={collapsed ? `${folder.name}${unreadText}` : undefined}
                aria-label={`${folder.name}${unreadText}`}
                aria-current={folder.id === selected ? "true" : undefined}
              >
                <span className="folder-glyph" aria-hidden="true">
                  {glyphFor(folder)}
                </span>
                <span className="folder-name">{folder.name}</span>
                {badge ? (
                  <span className="folder-badge" data-dot={badge.dot ? "true" : "false"}>
                    {badge.text}
                  </span>
                ) : null}
                {collapsed ? (
                  <span className="folder-tooltip" role="tooltip">
                    {folder.name}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>

      {/* Pinned to the bottom of the *visible* pane, not to the bottom of the
          folder list: this is the line that answers "is my mail current?", and
          it is the one line that must never be the thing you have to scroll for. */}
      <p className="folder-sync" data-state={syncState} role="status">
        <span className="sync-dot" aria-hidden="true" data-state={syncState} />
        <span>
          {syncLine}
          {unreadTotal > 0 ? ` · ${unreadTotal} unread` : ""}
        </span>
      </p>
    </nav>
  );
}