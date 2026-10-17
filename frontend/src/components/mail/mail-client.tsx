"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  api,
  getMessage,
  getThread,
  listFolders,
  listMailboxes,
  listMessages,
  type Folder,
  type Mailbox,
  type MessageDetail,
  type MessageFilter,
  type MessageRow,
} from "../../lib/api";
import FolderPane from "./folder-pane";
import MessageListPane from "./message-list-pane";
import ReadingPane from "./reading-pane";

type MobilePane = "folders" | "list" | "reading";
type SyncState = "complete" | "indexing" | "unknown";

type SyncStatus = { state: string; last_success_at: string | null };

const PAGE_SIZE = 50;

export default function MailClient() {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [mailboxId, setMailboxId] = useState("");
  const [connectionError, setConnectionError] = useState<"no-mailbox" | "failed" | null>(null);

  const [folders, setFolders] = useState<Folder[]>([]);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [foldersCollapsed, setFoldersCollapsed] = useState(false);
  const [syncState, setSyncState] = useState<SyncState>("unknown");
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);

  const [filter, setFilter] = useState<MessageFilter>("all");
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [listLoadingMore, setListLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [thread, setThread] = useState<MessageRow[]>([]);
  const [highlightQuote, setHighlightQuote] = useState<string | null>(null);

  const [accountOpen, setAccountOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<MobilePane>("list");

  // Guard against re-fetching the same folder/filter pair.
  const loadedKey = useRef<string>("");
  // Exposed so the OL2 Ask bar and the "/" shortcut can focus the same input.
  const askInputRef = useRef<HTMLInputElement | null>(null);

  // ---------------------------------------------------------------- mailboxes
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const all = await listMailboxes();
        const connected = all.filter((mailbox) => mailbox.status === "active");
        if (!active) return;
        setMailboxes(connected);
        setMailboxId((current) =>
          connected.some((mailbox) => mailbox.id === current) ? current : (connected[0]?.id ?? ""),
        );
        setConnectionError(connected.length === 0 ? "no-mailbox" : null);
      } catch {
        if (active) setConnectionError("failed");
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // ------------------------------------------------------- folders + sync state
  const refreshFolders = useCallback(async () => {
    if (!mailboxId) return;
    try {
      const payload = await listFolders(mailboxId);
      setFolders(payload.folders);
      setUnreadTotal(payload.unread_total);
      setFolderId((current) => {
        if (current && payload.folders.some((folder) => folder.id === current)) return current;
        const first = payload.folders.find((folder) => folder.total > 0) ?? payload.folders[0];
        return first?.id ?? null;
      });
    } catch (error) {
      setListError(error instanceof Error ? error.message : "Could not load folders.");
    }
  }, [mailboxId]);

  useEffect(() => {
    if (!mailboxId) return;
    let active = true;
    (async () => {
      try {
        const status = await api<SyncStatus>(`/api/mailboxes/${encodeURIComponent(mailboxId)}/sync`);
        if (!active) return;
        setSyncState(status.state === "indexing" ? "indexing" : "complete");
        setLastSyncedAt(status.last_success_at);
      } catch {
        if (active) setSyncState("unknown");
      }
    })();
    void refreshFolders();
    return () => {
      active = false;
    };
  }, [mailboxId, refreshFolders]);

  // ------------------------------------------------------------- message list
  const loadPage = useCallback(
    async (folder: string, activeFilter: MessageFilter, pageCursor?: string | null) => {
      if (!mailboxId) return;
      const appending = Boolean(pageCursor);
      if (appending) setListLoadingMore(true);
      else setListLoading(true);
      setListError(null);
      try {
        const page = await listMessages({
          mailboxId,
          folder,
          filter: activeFilter,
          cursor: pageCursor,
          limit: PAGE_SIZE,
        });
        setMessages((current) => (appending ? [...current, ...page.messages] : page.messages));
        setCursor(page.next_cursor);
        setHasMore(Boolean(page.next_cursor));
      } catch (error) {
        setListError(error instanceof Error ? error.message : "Could not load messages.");
      } finally {
        if (appending) setListLoadingMore(false);
        else setListLoading(false);
      }
    },
    [mailboxId],
  );

  useEffect(() => {
    if (!mailboxId || !folderId) return;
    const key = `${folderId}|${filter}`;
    if (loadedKey.current === key) return;
    loadedKey.current = key;
    setMessages([]);
    setCursor(null);
    setHasMore(false);
    void loadPage(folderId, filter);
  }, [mailboxId, folderId, filter, loadPage]);

  const loadMore = useCallback(() => {
    if (!folderId || !cursor || listLoadingMore) return;
    void loadPage(folderId, filter, cursor);
  }, [folderId, filter, cursor, listLoadingMore, loadPage]);

  // ------------------------------------------------------------------ reading
  const openMessage = useCallback(
    async (id: string) => {
      if (!mailboxId) return;
      setSelectedId(id);
      setDetailLoading(true);
      setDetailError(null);
      setHighlightQuote(null);
      setMobilePane("reading");
      try {
        const payload = await getMessage(mailboxId, id);
        setDetail(payload);
        const key = payload.conversation_id || payload.thread_key;
        if (!key) {
          setThread([]);
          return;
        }
        try {
          const conversation = await getThread(mailboxId, key);
          setThread(conversation.messages);
        } catch {
          // A thread that will not load must not block reading the message.
          setThread([]);
        }
      } catch (error) {
        setDetail(null);
        setThread([]);
        setDetailError(
          error instanceof ApiError && error.status === 404
            ? "That message is no longer available."
            : error instanceof Error
              ? error.message
              : "Could not open that message.",
        );
      } finally {
        setDetailLoading(false);
      }
    },
    [mailboxId],
  );

  const selectMessage = useCallback(
    (id: string) => {
      if (id === selectedId) return;
      void openMessage(id);
    },
    [selectedId, openMessage],
  );

  // ----------------------------------------------------------------- keyboard
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        !!target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        askInputRef.current?.focus();
        return;
      }
      if (event.key === "/" && !typing) {
        event.preventDefault();
        askInputRef.current?.focus();
        return;
      }
      if (typing) {
        if (event.key === "Escape") (target as HTMLInputElement).blur();
        return;
      }
      if (event.key === "Escape") {
        if (selectedId) {
          setSelectedId(null);
          setDetail(null);
          setThread([]);
        }
        setMobilePane("list");
        return;
      }
      const forward = event.key === "j" || event.key === "ArrowDown";
      const backward = event.key === "k" || event.key === "ArrowUp";
      if ((forward || backward) && messages.length > 0) {
        event.preventDefault();
        const index = messages.findIndex((row) => row.id === selectedId);
        const delta = forward ? 1 : -1;
        const next = index === -1 ? 0 : Math.min(messages.length - 1, Math.max(0, index + delta));
        void openMessage(messages[next].id);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [messages, selectedId, openMessage]);

  const selectedMailbox = useMemo(
    () => mailboxes.find((mailbox) => mailbox.id === mailboxId) ?? null,
    [mailboxes, mailboxId],
  );

  if (connectionError === "no-mailbox") {
    return (
      <main className="mail-page">
        <div className="pane-empty connect-prompt">
          <h1>No mailbox connected</h1>
          <p>Connect a read-only Outlook mailbox to browse and search your mail.</p>
          <a className="button-secondary" href="/app/settings">
            Connect a mailbox
          </a>
        </div>
      </main>
    );
  }

  if (connectionError === "failed") {
    return (
      <main className="mail-page">
        <div className="pane-empty" role="alert">
          <p>We could not reach your account. Check your connection and try again.</p>
          <button
            type="button"
            className="button-secondary"
            onClick={() => window.location.reload()}
          >
            Try again
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="mail-page" data-pane={mobilePane}>
      <div className="mail-panes">
        <FolderPane
          folders={folders}
          selected={folderId}
          unreadTotal={unreadTotal}
          syncState={syncState}
          lastSyncedAt={lastSyncedAt}
          onSelect={(id) => {
            setFolderId(id);
            loadedKey.current = "";
            setMobilePane("list");
          }}
          onToggle={() => setFoldersCollapsed((value) => !value)}
          collapsed={foldersCollapsed}
        />

        <MessageListPane
          messages={messages}
          selectedId={selectedId}
          filter={filter}
          loading={listLoading}
          loadingMore={listLoadingMore}
          hasMore={hasMore}
          error={listError}
          sentFolder={(folderId ?? "").toLowerCase() === "sentitems"}
          resultMode={false}
          emptyMessage={
            syncState === "indexing"
              ? "Sync in progress: results may be incomplete."
              : folderId
                ? null
                : "No folders have been synced yet."
          }
          onFilter={setFilter}
          onSelect={selectMessage}
          onLoadMore={loadMore}
        />

        <ReadingPane
          detail={detail}
          loading={detailLoading}
          error={detailError}
          mailboxId={mailboxId || null}
          thread={thread}
          highlightQuote={highlightQuote}
          onOpenThreadMessage={(id) => void openMessage(id)}
          onRetry={() => selectedId && void openMessage(selectedId)}
        />
      </div>

      {/* Mobile single-pane navigation; hidden on desktop by CSS. */}
      <div className="mobile-bar">
        <button
          type="button"
          className="text-button"
          onClick={() => setMobilePane(mobilePane === "reading" ? "list" : "folders")}
        >
          <span aria-hidden="true">←</span> Back
        </button>
        <span className="mobile-pane-label">
          {mobilePane === "folders" ? "Folders" : mobilePane === "list" ? (folderId ?? "") : "Message"}
        </span>
        {mobilePane === "list" ? (
          <button type="button" className="text-button" onClick={() => setMobilePane("folders")}>
            Folders
          </button>
        ) : null}
      </div>

      {/* Ask bar lands in OL2; the ref above already points at its input. */}
      <div className="account-menu-wrapper" hidden>
        <button type="button" onClick={() => setAccountOpen((value) => !value)}>
          {selectedMailbox?.email ?? "Account"}
        </button>
        {accountOpen ? <div role="menu" /> : null}
      </div>
    </main>
  );
}