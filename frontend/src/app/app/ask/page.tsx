"use client";
import Link from "next/link";
import { FormEvent, useState } from "react";
import { api } from "../../../lib/api";
import { useConnectedMailbox } from "../../../lib/use-connected-mailbox";

type AskResult = {
  answer?: string | null;
  explanation?: string;
  response_type?: "timeline";
  timeline?: { date: string | null; email_id: string; subject: string; sender: string; text: string; quote: string; outlook_url?: string | null }[];
  claims?: { text: string; type: "explicit" | "inferred"; email_ids: string[]; quote: string }[];
  closest_emails?: { email_id: string; subject: string; sender: string; received_at: string; outlook_url?: string | null }[];
};

const questionIdeas = [
  "Give me a gentle summary of my recent conversations",
  "What should I follow up on?",
  "What did we decide about…?",
];

export default function Ask() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResult>();
  const [askError, setAskError] = useState("");
  const [asking, setAsking] = useState(false);
  const { mailboxes, mailboxId, setMailboxId, loading: mailboxesLoading, error: mailboxError } = useConnectedMailbox();
  const selectedMailbox = mailboxes.find(mailbox => mailbox.id === mailboxId);

  async function ask(e: FormEvent) {
    e.preventDefault();
    setAsking(true);
    setAskError("");
    setResult(undefined);
    try {
      const response = await api<AskResult>("/api/assistant/ask", {
        method: "POST",
        body: JSON.stringify({ mailbox_id: mailboxId, question }),
      });
      setResult(response);
    } catch (error) {
      setAskError(error instanceof Error ? error.message : "Could not get an answer. Please try again.");
    } finally {
      setAsking(false);
    }
  }

  return (
    <main>
      <div className="page-intro">
        <h1>Ask your inbox</h1>
        <p className="page-description">Ask in your own words. I’ll look through your latest synced Outlook emails and bring the relevant conversations together in a friendly summary.</p>
      </div>
      <form className="form-panel form-grid" onSubmit={ask}>
        {mailboxes.length > 1 && (
          <label>
            Outlook account
            <select value={mailboxId} onChange={e => setMailboxId(e.target.value)} required>
              {mailboxes.map(mailbox => <option key={mailbox.id} value={mailbox.id}>{mailbox.email}</option>)}
            </select>
          </label>
        )}
        {mailboxes.length === 1 && <p className="connected-account">Asking about <strong>{mailboxes[0].email}</strong></p>}
        <div className="question-input">
          <label htmlFor="email-question">What would you like to know?</label>
          <textarea
            id="email-question"
            aria-label="Question"
            placeholder="Try asking a full question, like “What did the team agree about the launch date?”"
            value={question}
            onChange={e => setQuestion(e.target.value)}
            minLength={3}
            maxLength={1000}
            required
            rows={3}
          />
        </div>
        <div className="question-ideas" aria-label="Example questions">
          <span className="muted">Try asking</span>
          {questionIdeas.map(idea => (
            <button className="question-idea" key={idea} type="button" onClick={() => setQuestion(idea)}>
              {idea}
            </button>
          ))}
        </div>
        <div className="button-row">
          <button disabled={mailboxesLoading || asking || !mailboxId || !selectedMailbox?.ai_consent}>{asking ? "Putting it together…" : mailboxesLoading ? "Loading account…" : "Ask my emails"}</button>
        </div>
      </form>
      {mailboxError && <p className="status-message" role="alert">{mailboxError}</p>}
      {!mailboxesLoading && !mailboxError && mailboxes.length === 0 && (
        <p className="status-message">Connect Outlook to ask questions about your email. <Link href="/app/settings">Go to Settings</Link></p>
      )}
      {selectedMailbox && !selectedMailbox.ai_consent && <p className="status-message">Enable AI answers for this mailbox in <Link href="/app/settings">Settings</Link> to continue.</p>}
      {askError && <p className="status-message" role="alert">{askError}</p>}
      {result && (
        <section className="card answer-card" aria-live="polite">
          <div className="answer-heading">
            <span className="answer-avatar" aria-hidden="true">O</span>
            <h2>{result.answer ? "Here’s what I found" : "Let’s try another angle"}</h2>
          </div>
          <p>{result.answer || result.explanation}</p>
          {result.response_type === "timeline" && result.timeline && result.timeline.length > 0 && (
            <div className="answer-sources">
              <h3>Timeline</h3>
              <ol className="answer-takeaways">
                {result.timeline.map((event, index) => (
                  <li key={`${event.email_id}-${index}`}>
                    <span><strong>{event.date || "Date unavailable"}</strong> · {event.text}</span>
                    <blockquote>“{event.quote}”</blockquote>
                    <span className="muted">
                      {event.sender} ·{" "}
                      {event.outlook_url
                        ? <a href={event.outlook_url} target="_blank" rel="noopener noreferrer">{event.subject || "(no subject)"}</a>
                        : event.subject || "(no subject)"}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {result.response_type !== "timeline" && result.claims && result.claims.length > 0 && (
            <ul className="answer-takeaways">
              {result.claims.map((claim, index) => (
                <li key={`${claim.email_ids.join("-")}-${index}`}>
                  <span>{claim.text}</span>
                  <blockquote>“{claim.quote}”</blockquote>
                </li>
              ))}
            </ul>
          )}
          {result.closest_emails && result.closest_emails.length > 0 && (
            <div className="answer-sources">
              <h3>Emails I used</h3>
              <ul>
                {result.closest_emails.map(email => (
                  <li key={email.email_id}>
                    <div className="source-email">
                      {email.outlook_url
                        ? <a href={email.outlook_url} target="_blank" rel="noopener noreferrer">{email.subject || "(no subject)"}</a>
                        : <span>{email.subject || "(no subject)"}</span>}
                      <span className="muted">{email.sender} · {email.received_at}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
