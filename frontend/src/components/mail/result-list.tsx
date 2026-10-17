"use client";

import type { AskClaim, Confidence, SearchCard } from "../../lib/api";
import { formatStamp } from "./message-list-pane";

export const CONFIDENCE_COPY: Record<Confidence, string> = {
  // Never "exact": the backend cannot produce it and the UI must not invent a
  // certainty the retrieval never had.
  high: "Strong match",
  low: "Possible match",
  none: "No reliable match",
};

/**
 * One row per result, with the confidence label and the reasons it matched.
 * The reasons are what let a reader judge a match instead of trusting a badge.
 */
export default function ResultList({
  results,
  confidence,
  bestEmailId,
  selectedId,
  message,
  onSelect,
  onReword,
}: {
  results: SearchCard[];
  confidence: Confidence;
  bestEmailId: string | null;
  selectedId: string | null;
  message: string | null;
  onSelect: (id: string) => void;
  onReword: () => void;
}) {
  if (results.length === 0) {
    return (
      <div className="result-state" role="status">
        <p className="result-confidence" data-confidence={confidence}>
          {CONFIDENCE_COPY[confidence]}
        </p>
        <p>{message ?? "No reliable match found."}</p>
        <p className="muted">Try naming the sender, a rough date, or fewer words.</p>
        <button type="button" className="button-secondary" onClick={onReword}>
          Reword the search
        </button>
      </div>
    );
  }

  return (
    <div className="result-state-list">
      <p className="result-confidence" data-confidence={confidence}>
        {CONFIDENCE_COPY[confidence]}
      </p>
      {confidence === "low" ? (
        <p className="muted result-hint">
          Several messages could match. These are ranked best first.
        </p>
      ) : null}
      {confidence === "none" ? (
        <p className="muted result-hint">
          None of these is a reliable match. They are the closest ones I found, ranked
          best first.
        </p>
      ) : null}
      <ul className="message-list" role="listbox" aria-label="Search results">
        {results.map((result, index) => {
          const isBest = result.email_id === bestEmailId;
          return (
            <li
              key={result.email_id}
              className="message-row"
              data-selected={result.email_id === selectedId ? "true" : "false"}
              data-message-id={result.email_id}
              role="option"
              aria-selected={result.email_id === selectedId}
              tabIndex={result.email_id === selectedId ? 0 : -1}
              onClick={() => onSelect(result.email_id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(result.email_id);
                }
              }}
            >
              <span className="unread-dot" aria-hidden="true" />
              <span className="message-main">
                <span className="message-topline">
                  <span className="message-sender">{result.sender}</span>
                  {isBest ? <span className="best-match-tag">best match</span> : null}
                  <time className="message-stamp" dateTime={result.received_at}>
                    {formatStamp(result.received_at)}
                  </time>
                </span>
                <span className="message-subject">{result.subject || "(no subject)"}</span>
                <span className="message-preview">
                  {result.preview ? result.preview.replace(/\s+/g, " ").trim() : ""}
                </span>
                {result.reasons.length > 0 ? (
                  <span className="reason-chips">
                    {result.reasons.map((reason, reasonIndex) => (
                      <span className="reason-chip" key={`${reason.type}-${reasonIndex}`}>
                        <span className="reason-type">{reason.type}</span>
                        {reason.detail}
                      </span>
                    ))}
                  </span>
                ) : null}
              </span>
              <span className="result-rank" aria-hidden="true">
                {index + 1}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * The answer card. Claims carry citation markers that select the supporting
 * email and surface the quoted passage, so a reader can always get from a
 * sentence back to the message it came from.
 */
export function AnswerCard({
  answer,
  claims,
  sufficient,
  hasResults,
  selectedClaim,
  onCitation,
}: {
  answer: string;
  claims: AskClaim[];
  sufficient: boolean;
  /** Whether retrieved candidates are listed alongside this card. */
  hasResults: boolean;
  selectedClaim: number | null;
  onCitation: (index: number) => void;
}) {
  // Number the distinct emails in first-mention order so [1], [2] read naturally.
  const order: string[] = [];
  for (const claim of claims) {
    for (const id of claim.email_ids) {
      if (!order.includes(id)) order.push(id);
    }
  }
  const marker = (id: string) => {
    const index = order.indexOf(id);
    return index === -1 ? null : index + 1;
  };

  // "I could not find this" is only true when nothing came back. With
  // candidates on screen the honest framing is "could not answer from these".
  const heading = sufficient
    ? "From your emails"
    : hasResults
      ? "I could not answer that"
      : "I could not find this";

  return (
    <section className="answer-card" aria-label="Answer" aria-live="polite">
      <h2 className="answer-heading">{heading}</h2>
      {answer ? (
        <div className="answer-body">
          {claims.length === 0 ? (
            <p>{answer}</p>
          ) : (
            claims.map((claim, index) => (
              <div className="answer-claim" key={`${claim.text}-${index}`}>
                <p className="answer-claim-text">{claim.text}</p>
                <blockquote className="answer-claim-quote">
                  {claim.quote}
                  <span className="answer-claim-marks">
                    {claim.email_ids.map((id) => {
                      const number = marker(id);
                      if (number === null) return null;
                      return (
                        <button
                          key={id}
                          type="button"
                          className="citation-button"
                          data-active={selectedClaim === index ? "true" : "false"}
                          aria-label={`Open source ${number}`}
                          onClick={() => onCitation(index)}
                        >
                          [{number}]
                        </button>
                      );
                    })}
                  </span>
                </blockquote>
              </div>
            ))
          )}
        </div>
      ) : null}
      {!sufficient ? (
        <p className="muted">
          {hasResults
            ? "The messages below are the closest ones I found. Try naming a sender, a date, or a specific topic."
            : "I could not find this in your emails. Try different words, or ask about a specific message."}
        </p>
      ) : null}
    </section>
  );
}