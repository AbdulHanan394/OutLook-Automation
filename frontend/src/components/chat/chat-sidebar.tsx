"use client";

import { useEffect, useRef, useState } from "react";
import {
  ChatSummary,
  deleteChat,
  listChats,
  searchChats,
  updateChat,
} from "../../lib/chats";

/** "Today", "Tue 4 Mar" or "4 Mar 2025", depending on how far back it is. */
function stamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000);
  if (days <= 0) {
    return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  if (days < 7) return date.toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(date.getFullYear() === new Date().getFullYear() ? {} : { year: "numeric" as const }),
  });
}

type Props = {
  chats: ChatSummary[];
  activeId: string | null;
  temporary: boolean;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onToggleTemporary: () => void;
  onChanged: (chats: ChatSummary[]) => void;
  searchToken: number;
};

function groupByWhen(chats: ChatSummary[]): { label: string; items: ChatSummary[] }[] {
  const now = Date.now();
  const buckets = new Map<string, ChatSummary[]>();
  const order: string[] = [];
  for (const chat of chats) {
    const age = now - new Date(chat.updated_at).getTime();
    const day = 86_400_000;
    const label =
      age < day ? "Today" : age < 7 * day ? "Previous 7 days" : age < 30 * day ? "Previous 30 days" : "Older";
    if (!buckets.has(label)) {
      buckets.set(label, []);
      order.push(label);
    }
    buckets.get(label)!.push(chat);
  }
  return order.map((label) => ({ label, items: buckets.get(label)! }));
}

export default function ChatSidebar({
  chats,
  activeId,
  temporary,
  onSelect,
  onNewChat,
  onToggleTemporary,
  onChanged,
  searchToken,
}: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ChatSummary[] | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [menuId, setMenuId] = useState<string | null>(null);
  const editRef = useRef<HTMLInputElement | null>(null);

  // Search is server-side full text over titles and message bodies, so it can
  // find a chat by a word from any turn. Debounced because it is a round trip.
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      searchChats(trimmed)
        .then((found) => {
          if (!cancelled) setResults(found);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        });
    }, 220);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, searchToken]);

  useEffect(() => {
    if (editingId && editRef.current) editRef.current.focus();
  }, [editingId]);

  useEffect(() => {
    if (!menuId) return;
    const close = () => setMenuId(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menuId]);

  async function commitRename(chat: ChatSummary) {
    const title = draftTitle.trim();
    setEditingId(null);
    if (!title || title === chat.title) return;
    const updated = await updateChat(chat.id, { title });
    onChanged(chats.map((item) => (item.id === chat.id ? updated : item)));
  }

  async function act(chat: ChatSummary, action: "pin" | "delete") {
    setMenuId(null);
    if (action === "delete") {
      await deleteChat(chat.id);
      onChanged(chats.filter((item) => item.id !== chat.id));
      if (activeId === chat.id) onSelect("");
      return;
    }
    const updated = await updateChat(chat.id, { pinned: !chat.pinned });
    onChanged(chats.map((item) => (item.id === chat.id ? updated : item)));
  }

  const visible = results ?? chats;
  const pinned = visible.filter((chat) => chat.pinned);
  const rest = visible.filter((chat) => !chat.pinned);
  const grouped = groupByWhen(rest);

  return (
    <nav className="chat-sidebar" aria-label="Chat history">
      <div className="chat-sidebar-actions">
        <button type="button" className="chat-new" onClick={onNewChat}>
          <span aria-hidden="true">+</span> New chat
        </button>
        <button
          type="button"
          className="chat-temporary"
          aria-pressed={temporary}
          onClick={onToggleTemporary}
          title="A temporary chat is not saved to your history"
        >
          Temporary
        </button>
      </div>

      <div className="chat-search">
        <label className="sr-only" htmlFor="chat-history-search">
          Search your chat history
        </label>
        <input
          id="chat-history-search"
          type="search"
          placeholder="Search history…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {query.trim() && (
          <p className="chat-search-status" role="status">
            {results === null ? "Searching…" : `${visible.length} ${visible.length === 1 ? "chat" : "chats"} found`}
          </p>
        )}
      </div>

      <div className="chat-list">
        {temporary && (
          <p className="chat-notice">
            <strong>Temporary chat.</strong> Nothing is saved and nothing is added to your memory.
          </p>
        )}

        {visible.length === 0 && query.trim() && (
          <p className="chat-empty">No chat matches “{query.trim()}”.</p>
        )}

        {!query.trim() && chats.length === 0 && !temporary && (
          <p className="chat-empty">Your conversations will appear here.</p>
        )}

        {pinned.length > 0 && (
          <section aria-label="Pinned">
            <h2 className="chat-group">Pinned</h2>
            <ul>
              {pinned.map((chat) => (
                <ChatRow
                  key={chat.id}
                  chat={chat}
                  active={chat.id === activeId}
                  editing={editingId === chat.id}
                  draftTitle={draftTitle}
                  setDraftTitle={setDraftTitle}
                  menuOpen={menuId === chat.id}
                  setMenuOpen={(open) => setMenuId(open ? chat.id : null)}
                  onSelect={onSelect}
                  onStartRename={() => {
                    setEditingId(chat.id);
                    setDraftTitle(chat.title);
                    setMenuId(null);
                  }}
                  onCommitRename={() => void commitRename(chat)}
                  onAct={(action) => void act(chat, action)}
                />
              ))}
            </ul>
          </section>
        )}

        {grouped.map((group) => (
          <section key={group.label} aria-label={group.label}>
            <h2 className="chat-group">{group.label}</h2>
            <ul>
              {group.items.map((chat) => (
                <ChatRow
                  key={chat.id}
                  chat={chat}
                  active={chat.id === activeId}
                  editing={editingId === chat.id}
                  draftTitle={draftTitle}
                  setDraftTitle={setDraftTitle}
                  menuOpen={menuId === chat.id}
                  setMenuOpen={(open) => setMenuId(open ? chat.id : null)}
                  onSelect={onSelect}
                  onStartRename={() => {
                    setEditingId(chat.id);
                    setDraftTitle(chat.title);
                    setMenuId(null);
                  }}
                  onCommitRename={() => void commitRename(chat)}
                  onAct={(action) => void act(chat, action)}
                />
              ))}
            </ul>
          </section>
        ))}
      </div>
    </nav>
  );
}

type RowProps = {
  chat: ChatSummary;
  active: boolean;
  editing: boolean;
  draftTitle: string;
  setDraftTitle: (value: string) => void;
  menuOpen: boolean;
  setMenuOpen: (open: boolean) => void;
  onSelect: (id: string) => void;
  onStartRename: () => void;
  onCommitRename: () => void;
  onAct: (action: "pin" | "delete") => void;
};

function ChatRow({
  chat,
  active,
  editing,
  draftTitle,
  setDraftTitle,
  menuOpen,
  setMenuOpen,
  onSelect,
  onStartRename,
  onCommitRename,
  onAct,
}: RowProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (editing && inputRef.current) inputRef.current.focus();
  }, [editing]);

  if (editing) {
    return (
      <li className="chat-row chat-row-editing">
        <input
          ref={inputRef}
          aria-label={`Rename ${chat.title}`}
          value={draftTitle}
          maxLength={120}
          onChange={(event) => setDraftTitle(event.target.value)}
          onBlur={onCommitRename}
          onKeyDown={(event) => {
            if (event.key === "Enter") onCommitRename();
            if (event.key === "Escape") setDraftTitle(chat.title);
          }}
        />
      </li>
    );
  }

  return (
    <li className={`chat-row${active ? " chat-row-active" : ""}`}>
      <button
        type="button"
        className="chat-row-open"
        aria-current={active ? "true" : undefined}
        onClick={() => onSelect(chat.id)}
      >
        <span className="chat-row-title">{chat.pinned ? <span aria-hidden="true">📌 </span> : null}{chat.title}</span>
        <span className="chat-row-time">{stamp(chat.updated_at)}</span>
      </button>
      <button
        type="button"
        className="chat-row-menu"
        aria-label={`Options for ${chat.title}`}
        aria-expanded={menuOpen}
        onClick={(event) => {
          event.stopPropagation();
          setMenuOpen(!menuOpen);
        }}
      >
        <span aria-hidden="true">⋯</span>
      </button>
      {menuOpen && (
        <div className="chat-row-actions" role="menu">
          <button type="button" role="menuitem" onClick={onStartRename}>
            Rename
          </button>
          <button type="button" role="menuitem" onClick={() => onAct("pin")}>
            {chat.pinned ? "Unpin" : "Pin"}
          </button>
          <button type="button" role="menuitem" onClick={() => onAct("delete")}>
            Delete
          </button>
        </div>
      )}
    </li>
  );
}

/** Loads the history once; the page owns the array so the sidebar stays pure. */
export function useChatHistory() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listChats()
      .then((found) => {
        if (cancelled) return;
        setChats(found);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : "Could not load your chats.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return { chats, setChats, loading, error, refresh: () => setToken((value) => value + 1) };
}