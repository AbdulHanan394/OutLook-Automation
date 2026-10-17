"use client";

import { useEffect, useRef } from "react";
import type { MessageFilter, MessageRow } from "../../lib/api";

const GROUPS: { key: string; label: string; test: (d: Date, now: Date) => boolean }[] = [
  { key: "today", label: "Today", test: (d, now) => d.toDateString() === now.toDateString() },
  {
    key: "yesterday",
    label: "Yesterday",
    test: (d, now) => {
      const yesterday = new Date(now);
      yesterday.setDate(yesterday.getDate() - 1);
      return d.toDateString() === yesterday.toDateString();
    },
  },
  {
    key: "this-week",
    label: "This week",
    test: (d, now) => {
      const start = new Date(now);
      start.setHours(0, 0, 0, 0);
      start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
      return d >= start && d.toDateString() !== now.toDateString();
    },
  },
  {
    key: "last-week",
    label: "Last week",
    test: (d, now) => {
      const start = new Date(now);
      start.setHours(0, 0, 0, 0);
      start.setDate(start.getDate() - ((start.getDay() + 6) % 7) - 7);
      const end = new Date(start);
      end.setDate(end.getDate() + 7);
      return d >= start && d < end;
    },
  },
  { key: "older", label: "Older", test: () => true },
];

function groupFor(iso: string, now: Date): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "older";
  for (const group of GROUPS) {
    if (group.test(date, now)) return group.key;
  }
  return "older";
}

/** Today shows a time; anything older shows a date, the way a mail client does. */
export function formatStamp(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

const FILTERS: { id: MessageFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "unread", label: "Unread" },
  { id: "has_attachments", label: "Has attachments" },
];

export default function MessageListPane({
  messages,
  selectedId,
  filter,
  loading,
  loadingMore,
  hasMore,
  error,
  sentFolder,
  resultMode,
  emptyMessage,
  onFilter,
  onSelect,
  onLoadMore,
}: {
  messages: MessageRow[];
  selectedId: string | null;
  filter: MessageFilter;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  error: string | null;
  sentFolder: boolean;
  resultMode: boolean;
  emptyMessage: string | null;
  onFilter: (filter: MessageFilter) => void;
  onSelect: (id: string) => void;
  onLoadMore: () => void;
}) {
  const sentinel = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const now = new Date();

  // Infinite scroll: fetch the next page when the sentinel scrolls into view.
  useEffect(() => {
    if (!hasMore || loading || loadingMore) return;
    const target = sentinel.current;
    if (!target || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
      },
      { root: null, rootMargin: "240px" },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [hasMore, loading, loadingMore, onLoadMore]);

  // Roving focus follows the selected row so arrow keys and screen readers agree.
  useEffect(() => {
    if (!selectedId) return;
    const node = listRef.current?.querySelector<HTMLElement>(`[data-message-id="${selectedId}"]`);
    node?.focus();
  }, [selectedId]);

  const grouped: { key: string; label: string; rows: MessageRow[] }[] = [];
  for (const row of messages) {
    const key = groupFor(row.received_at, now);
    const last = grouped[grouped.length - 1];
    if (last && last.key === key) last.rows.push(row);
    else grouped.push({ key, label: GROUPS.find((g) => g.key === key)!.label, rows: [row] });
  }

  const rowCount = messages.length;

  return (
    <section className="list-pane" aria-label="Messages" aria-busy={loading}>
      {!resultMode ? (
        <div className="quick-filters" role="group" aria-label="Quick filters">
          {FILTERS.map((option) => (
            <button
              key={option.id}
              type="button"
              className="quick-filter"
              aria-pressed={filter === option.id}
              onClick={() => onFilter(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}

      <div className="list-scroll" ref={listRef} tabIndex={-1}>
        {error ? (
          <p className="pane-empty" role="alert">
            {error}
          </p>
        ) : loading && rowCount === 0 ? (
          <ul className="message-list" aria-hidden="true">
            {Array.from({ length: 12 }, (_, index) => (
              <li className="message-row" key={index}>
                <span className="skeleton skeleton-line w-40" />
                <span className="skeleton skeleton-line w-60" />
              </li>
            ))}
          </ul>
        ) : rowCount === 0 ? (
          <p className="pane-empty">{emptyMessage ?? "No messages in this folder yet."}</p>
        ) : (
          <>
            {grouped.map((group) => (
              <section key={group.key} className="message-group" aria-label={group.label}>
                <h3 className="date-header" data-sticky="true">
                  {group.label}
                </h3>
                <ul className="message-list">
                  {group.rows.map((row) => {
                    const who = sentFolder
                      ? `to ${row.sender_name || row.sender_address || "unknown"}`
                      : row.sender_name || row.sender_address || "(unknown sender)";
                    return (
                      <li key={row.id}>
                        <div
                          className="message-row"
                          data-unread={row.is_read ? "false" : "true"}
                          data-selected={row.id === selectedId ? "true" : "false"}
                          data-message-id={row.id}
                          role="option"
                          aria-selected={row.id === selectedId}
                          tabIndex={row.id === selectedId ? 0 : -1}
                          onClick={() => onSelect(row.id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              onSelect(row.id);
                            }
                          }}
                        >
                          <span className="unread-dot" aria-hidden="true" />
                          <span className="message-main">
                            <span className="message-topline">
                              <span className="message-sender">{who}</span>
                              {row.importance === "high" ? (
                                <span className="importance-mark" title="High importance">
                                  <span aria-hidden="true">!</span>
                                  <span className="sr-only">High importance</span>
                                </span>
                              ) : null}
                              <time className="message-stamp" dateTime={row.received_at}>
                                {formatStamp(row.received_at, now)}
                              </time>
                            </span>
                            <span className="message-subject">
                              {row.subject || "(no subject)"}
                            </span>
                            <span className="message-preview">
                              {row.preview ? row.preview.replace(/\s+/g, " ").trim() : "(no preview)"}
                            </span>
                          </span>
                          {row.has_attachments ? (
                            <span className="attachment-mark" title="Has attachments">
                              <span aria-hidden="true">@</span>
                              <span className="sr-only">Has attachments</span>
                            </span>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
            <div ref={sentinel} className="scroll-sentinel" aria-hidden="true" />
            {loadingMore ? (
              <p className="pane-loading" role="status">
                Loading more messages…
              </p>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}