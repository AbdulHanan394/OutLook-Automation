"use client";

import { useEffect, useState } from "react";
import { messageRenderUrl, type MessageDetail, type MessageRow } from "../../lib/api";
import { formatStamp } from "./message-list-pane";

function addressOf(entry: { name: string | null; address: string | null }): string {
  if (entry.name && entry.address) return `${entry.name} <${entry.address}>`;
  return entry.name || entry.address || "";
}

export default function ReadingPane({
  detail,
  loading,
  error,
  mailboxId,
  thread,
  highlightQuote,
  onOpenThreadMessage,
  onRetry,
}: {
  detail: MessageDetail | null;
  loading: boolean;
  error: string | null;
  mailboxId: string | null;
  thread: MessageRow[];
  highlightQuote: string | null;
  onOpenThreadMessage: (id: string) => void;
  onRetry: () => void;
}) {
  // Images stay blocked until the reader asks, per message.
  const [images, setImages] = useState(false);
  const [showQuoted, setShowQuoted] = useState(false);
  const [copied, setCopied] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");

  // The iframe is an opaque origin: it cannot see data-theme, so the chosen
  // theme is read here and passed to the render endpoint explicitly.
  useEffect(() => {
    const read = () =>
      setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
    read();
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  // A different message means a different privacy decision.
  useEffect(() => {
    setImages(false);
    setShowQuoted(false);
  }, [detail?.id]);

  if (error) {
    return (
      <section className="reading-pane" aria-label="Reading pane">
        <div className="pane-empty" role="alert">
          <p>{error}</p>
          <button type="button" className="button-secondary" onClick={onRetry}>
            Try again
          </button>
        </div>
      </section>
    );
  }

  if (loading && !detail) {
    return (
      <section className="reading-pane" aria-label="Reading pane" aria-busy="true">
        <div className="reading-skeleton" aria-hidden="true">
          <span className="skeleton skeleton-line w-60" />
          <span className="skeleton skeleton-line w-40" />
          <span className="skeleton skeleton-line w-95" />
          <span className="skeleton skeleton-line w-80" />
        </div>
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="reading-pane" aria-label="Reading pane">
        <p className="pane-empty">Select a message to read it.</p>
      </section>
    );
  }

  const renderUrl =
    mailboxId === null
      ? null
      : messageRenderUrl(mailboxId, detail.id, { images, quoted: showQuoted, theme });

  const copyLink = async () => {
    if (!detail.web_link) return;
    try {
      await navigator.clipboard.writeText(detail.web_link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <article className="reading-pane" aria-label="Reading pane" aria-busy={loading}>
      <header className="reading-header">
        <h1 className="reading-subject">{detail.subject || "(no subject)"}</h1>
        <dl className="reading-headers">
          <div className="header-line">
            <dt>From</dt>
            <dd>
              {detail.sender_name || "(unknown)"}
              {detail.sender_address ? (
                <span className="header-address"> &lt;{detail.sender_address}&gt;</span>
              ) : null}
            </dd>
          </div>
          {detail.to.length > 0 ? (
            <div className="header-line">
              <dt>To</dt>
              <dd>{detail.to.map(addressOf).filter(Boolean).join(", ")}</dd>
            </div>
          ) : null}
          {detail.cc.length > 0 ? (
            <div className="header-line">
              <dt>Cc</dt>
              <dd>{detail.cc.map(addressOf).filter(Boolean).join(", ")}</dd>
            </div>
          ) : null}
          <div className="header-line">
            <dt>Date</dt>
            <dd>
              <time dateTime={detail.received_at}>
                {new Date(detail.received_at).toLocaleString()}
              </time>
            </dd>
          </div>
          {detail.attachment_names.length > 0 ? (
            <div className="header-line">
              <dt>Attachments</dt>
              <dd>
                <ul className="attachment-list">
                  {detail.attachment_names.map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
              </dd>
            </div>
          ) : null}
        </dl>

        {/* Read-only app: only actions that need no write permission. */}
        <div className="reading-actions">
          {detail.web_link ? (
            <a
              className="button-secondary"
              href={detail.web_link}
              target="_blank"
              rel="noopener noreferrer"
              title={detail.web_link}
            >
              Open in Outlook
            </a>
          ) : null}
          {detail.web_link ? (
            <button type="button" className="button-secondary" onClick={copyLink}>
              {copied ? "Link copied" : "Copy link"}
            </button>
          ) : null}
          {!images ? (
            <button type="button" className="text-button" onClick={() => setImages(true)}>
              Load images
            </button>
          ) : null}
          {detail.has_quoted && !showQuoted ? (
            <button type="button" className="text-button" onClick={() => setShowQuoted(true)}>
              Show quoted text
            </button>
          ) : null}
        </div>
      </header>

      {highlightQuote ? (
        <p className="citation-highlight">
          <span className="citation-label">Cited passage</span>
          <q>{highlightQuote}</q>
        </p>
      ) : null}

      <div className="message-body">
        {renderUrl ? (
          <iframe
            key={`${detail.id}:${images ? "img" : "noimg"}:${showQuoted ? "q" : "noq"}:${theme}`}
            className="body-frame"
            title={`Message body: ${detail.subject || "no subject"}`}
            // Empty sandbox: no scripts, no same-origin, no forms, no top-level
            // navigation. The rendered document is served by the API under its
            // own default-src 'none' policy. Neither layer trusts the other.
            sandbox=""
            referrerPolicy="no-referrer"
            src={renderUrl}
          />
        ) : (
          <pre className="body-text">{detail.body_text}</pre>
        )}
      </div>

      {thread.length > 1 ? (
        <section className="thread-section" aria-label="Conversation">
          <h2 className="pane-title">Conversation ({thread.length})</h2>
          <ul className="thread-list">
            {thread.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="thread-card"
                  data-current={item.id === detail.id ? "true" : "false"}
                  onClick={() => onOpenThreadMessage(item.id)}
                >
                  <span className="thread-card-top">
                    <span className="message-sender">
                      {item.sender_name || item.sender_address || "(unknown)"}
                    </span>
                    <time className="message-stamp" dateTime={item.received_at}>
                      {formatStamp(item.received_at)}
                    </time>
                  </span>
                  <span className="message-subject">{item.subject || "(no subject)"}</span>
                  <span className="message-preview">{item.preview}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}