"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { ChatCitation, ChatMessage } from "../../lib/chats";

/** The turn being written right now, held outside the saved transcript. */
export type StreamingTurn = { question: string; answer: string; started: boolean };

type Props = {
  messages: ChatMessage[];
  streaming: StreamingTurn | null;
  sending: boolean;
  notice: string | null;
  error: string | null;
  suggestions: string[];
  onSend: (text: string) => void;
  onOpenEmail: (emailId: string) => void;
  disabled?: boolean;
  disabledReason?: string;
  temporary: boolean;
};

const STARTERS = [
  "What did they say about the launch date?",
  "What should I follow up on this week?",
  "Summarise the most recent conversations",
];

export default function ChatPanel({
  messages,
  streaming,
  sending,
  notice,
  error,
  suggestions,
  onSend,
  onOpenEmail,
  disabled,
  disabledReason,
  temporary,
}: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  // The transcript is the only thing that should scroll, and only downwards.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, streaming?.answer]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending || disabled) return;
    setDraft("");
    onSend(text);
  }

  const empty = messages.length === 0 && !streaming;

  return (
    <section className="chat-panel" aria-label="Conversation">
      <div className="chat-transcript" role="log" aria-live="polite" aria-label="Messages">
        {empty && (
          <div className="chat-welcome">
            <h2>Ask about your email</h2>
            <p>
              Ask in your own words. I search your synced Outlook mail and answer with the emails I
              used &mdash; every quote is checked against the message it came from before you see it.
            </p>
            {temporary && (
              <p className="chat-notice">
                <strong>Temporary chat.</strong> Nothing is saved to your history and nothing is added
                to your memory.
              </p>
            )}
            <ul className="chat-starters">
              {STARTERS.map((starter) => (
                <li key={starter}>
                  <button type="button" onClick={() => setDraft(starter)}>
                    {starter}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {messages.map((message) => (
          <Turn key={message.id} message={message} onOpenEmail={onOpenEmail} />
        ))}

        {streaming && (
          <>
            <article className="chat-turn chat-turn-user">
              <header>
                <span className="chat-turn-role">You</span>
              </header>
              <p className="chat-turn-text">{streaming.question}</p>
            </article>
            <article className="chat-turn chat-turn-assistant" aria-busy="true">
              <header>
                <span className="chat-turn-role">Assistant</span>
              </header>
              {streaming.answer ? (
                <p className="chat-turn-text">
                  {streaming.answer}
                  <span className="chat-caret" aria-hidden="true" />
                </p>
              ) : (
                <p className="chat-turn-text muted" role="status">
                  <span className="button-spinner" aria-hidden="true" /> Searching your email&hellip;
                </p>
              )}
            </article>
          </>
        )}

        <div ref={endRef} />
      </div>

      {notice && (
        <p className="chat-notice" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="status-message" role="alert">
          {error}
        </p>
      )}

      {/*
        Suggestions sit above the composer and only ever *offer* something.
        Pressing one fills the box; nothing is sent until the reader presses
        Send, so there is no prefill and no message the reader did not write.
      */}
      {suggestions.length > 0 && !sending && (
        <ul className="chat-suggestions" aria-label="Suggested follow-ups">
          {suggestions.map((suggestion) => (
            <li key={suggestion}>
              <button type="button" onClick={() => setDraft(suggestion)}>
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      )}

      <form className="chat-composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="chat-message">
          Message the assistant
        </label>
        <textarea
          id="chat-message"
          value={draft}
          rows={1}
          maxLength={4000}
          placeholder="Ask a question about your email&hellip;"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          disabled={disabled || sending}
        />
        <button type="submit" disabled={disabled || sending || !draft.trim()}>
          {sending ? "Working…" : "Send"}
        </button>
      </form>
      {disabled && disabledReason && <p className="status-message">{disabledReason}</p>}
    </section>
  );
}

function Turn({
  message,
  onOpenEmail,
}: {
  message: ChatMessage;
  onOpenEmail: (emailId: string) => void;
}) {
  const citations: ChatCitation[] = message.citations ?? [];
  return (
    <article className={`chat-turn chat-turn-${message.role}`}>
      <header>
        <span className="chat-turn-role">{message.role === "user" ? "You" : "Assistant"}</span>
        <time dateTime={message.created_at}>
          {new Date(message.created_at).toLocaleTimeString(undefined, {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </time>
      </header>
      <p className="chat-turn-text">{message.text}</p>
      {citations.length > 0 && (
        <div className="chat-citations">
          <h3>Emails I used</h3>
          <ul>
            {citations.map((citation) => (
              <li key={citation.email_id}>
                <button type="button" onClick={() => onOpenEmail(citation.email_id)}>
                  <span className="chat-citation-subject">
                    {citation.subject || "(no subject)"}
                  </span>
                  <span className="muted">
                    {citation.sender} &middot; {citation.received_at}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}