"use client";

import { useRef, useState } from "react";
import type { AskChip, AskMode } from "../../lib/api";

const MODES: { id: AskMode; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "Decide whether to find an email or answer a question" },
  { id: "find", label: "Find", hint: "Locate a specific email" },
  { id: "ask", label: "Ask", hint: "Answer a question from your mail" },
];

export type AskStatus = "idle" | "interpreting" | "searching" | "writing";

const STATUS_TEXT: Record<AskStatus, string> = {
  idle: "",
  interpreting: "Interpreting",
  searching: "Searching your emails",
  writing: "Writing answer",
};

/**
 * The Ask bar.
 *
 * One input, three kinds of input. Operators go straight through as a
 * deterministic query, a natural-language description of a message goes through
 * the interpretation step, and a question comes back as an answer. Nothing here
 * decides which: the server does, and the mode toggle only overrides it.
 */
export default function AskBar({
  value,
  onChange,
  onSubmit,
  onClear,
  mode,
  onModeChange,
  status,
  chips,
  onRemoveChip,
  notice,
  active,
  suggestions,
  recentAsks,
  onSuggestion,
  onNewSearch,
  conversationOpen,
  onToggleConversation,
  sessionTurns,
  inputRef,
  disabled,
  disabledReason,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onClear: () => void;
  mode: AskMode;
  onModeChange: (mode: AskMode) => void;
  status: AskStatus;
  chips: AskChip[];
  onRemoveChip: (chip: AskChip) => void;
  notice: string | null;
  active: boolean;
  suggestions: string[];
  recentAsks: string[];
  onSuggestion: (value: string) => void;
  onNewSearch: () => void;
  conversationOpen: boolean;
  onToggleConversation: () => void;
  sessionTurns: string[];
  inputRef: React.RefObject<HTMLInputElement | null>;
  disabled: boolean;
  disabledReason: string | null;
}) {
  const [focused, setFocused] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const blurTimer = useRef<number | null>(null);

  const showSuggestions =
    focused && suggestionsOpen && value.trim() === "" && (suggestions.length > 0 || recentAsks.length > 0);

  const scheduleBlur = () => {
    blurTimer.current = window.setTimeout(() => setFocused(false), 140);
  };
  const cancelBlur = () => {
    if (blurTimer.current) window.clearTimeout(blurTimer.current);
  };

  return (
    <div className="ask-bar-region">
      <form
        className="ask-bar"
        role="search"
        aria-label="Ask your emails"
        onSubmit={(event) => {
          event.preventDefault();
          if (!disabled) onSubmit();
        }}
      >
        <span className="ask-glyph" aria-hidden="true">
          ?
        </span>
        <input
          ref={inputRef}
          className="ask-input"
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onFocus={() => {
            cancelBlur();
            setFocused(true);
            setSuggestionsOpen(true);
          }}
          onBlur={scheduleBlur}
          placeholder="Ask your emails, or search with from: subject: has:attachment"
          aria-label="Ask a question or describe an email to find"
          aria-describedby="ask-status"
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
          maxLength={1000}
        />

        <div className="ask-mode" role="group" aria-label="Ask mode">
          {MODES.map((option) => (
            <button
              key={option.id}
              type="button"
              className="ask-mode-button"
              aria-pressed={mode === option.id}
              title={option.hint}
              onClick={() => onModeChange(option.id)}
              disabled={disabled}
            >
              {option.label}
            </button>
          ))}
        </div>

        {active ? (
          <button type="button" className="ask-clear" onClick={onClear} aria-label="Clear search">
            <span aria-hidden="true">×</span>
          </button>
        ) : null}

        <button type="submit" className="ask-submit" disabled={disabled || value.trim() === ""}>
          {status === "idle" ? "Ask" : "…"}
        </button>

        {showSuggestions ? (
          <div className="ask-suggestions" role="listbox" aria-label="Suggested asks">
            {suggestions.length > 0 ? (
              <>
                <p className="ask-suggestion-heading">Try</p>
                <ul>
                  {suggestions.map((suggestion) => (
                    <li key={suggestion}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={false}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => onSuggestion(suggestion)}
                      >
                        {suggestion}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {recentAsks.length > 0 ? (
              <>
                <p className="ask-suggestion-heading">Recent</p>
                <ul>
                  {recentAsks.map((ask) => (
                    <li key={ask}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={false}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => onSuggestion(ask)}
                      >
                        {ask}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}
      </form>

      <div className="ask-meta">
        <p id="ask-status" className="ask-status" role="status" aria-live="polite">
          {status !== "idle" ? (
            <>
              <span className="ask-status-dot" aria-hidden="true" />
              {STATUS_TEXT[status]}…
            </>
          ) : disabled && disabledReason ? (
            disabledReason
          ) : (
            "Operators like from:, subject:, has:attachment, is:unread, before:, after:, folder:"
          )}
        </p>

        {chips.length > 0 || sessionTurns.length > 1 ? (
          <div className="ask-chips" aria-label="Applied filters">
            {chips.map((chip, index) => (
              <span
                key={`${chip.kind}-${chip.value}-${index}`}
                className="ask-chip"
                data-guessed={chip.guessed ? "true" : "false"}
              >
                {chip.guessed ? <span className="ask-chip-tag">guessed</span> : null}
                {chip.label}
                <button
                  type="button"
                  aria-label={`Remove filter ${chip.label}`}
                  onClick={() => onRemoveChip(chip)}
                >
                  <span aria-hidden="true">×</span>
                </button>
              </span>
            ))}

            {sessionTurns.length > 1 ? (
              <button
                type="button"
                className="ask-chip ask-chip-button"
                aria-expanded={conversationOpen}
                onClick={onToggleConversation}
              >
                Conversation ({sessionTurns.length})
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {notice ? (
        <p className="ask-notice" role="status">
          {notice}
        </p>
      ) : null}

      {conversationOpen && sessionTurns.length > 1 ? (
        <div className="conversation-drawer">
          <div className="conversation-head">
            <h2>Conversation</h2>
            <button type="button" className="text-button" onClick={onNewSearch}>
              New search
            </button>
          </div>
          <ol className="conversation-list">
            {sessionTurns.map((turn, index) => (
              <li key={`${turn}-${index}`}>{turn}</li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}