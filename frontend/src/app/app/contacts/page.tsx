"use client";

import { FormEvent, useState } from "react";
import { api } from "../../../lib/api";

type Contact = {
  address: string;
  message_count: number;
  sent_count: number;
  received_count: number;
  first_date: string | null;
  last_date: string | null;
  thread_count: number;
  threads: { thread_id: string; subject: string; message_count: number; first_date: string; last_date: string; sender: string; outlook_url: string | null }[];
  open_commitments: { id: string; text: string; due_date: string | null; due_text: string | null; subject: string; outlook_url: string | null }[];
};

type Brief = {
  answer?: string | null;
  explanation?: string;
  claims?: { text: string; type: "explicit" | "inferred"; email_ids: string[]; quote: string }[];
  sources?: { email_id: string; subject: string; date: string; outlook_url: string | null }[];
};

export default function ContactsPage() {
  const [address, setAddress] = useState("");
  const [contact, setContact] = useState<Contact>();
  const [brief, setBrief] = useState<Brief>();
  const [loading, setLoading] = useState(false);
  const [briefing, setBriefing] = useState(false);
  const [error, setError] = useState("");

  async function findContact(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setBrief(undefined);
    setContact(undefined);
    try {
      setContact(await api<Contact>(`/api/contacts/${encodeURIComponent(address.trim())}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load this contact.");
    } finally {
      setLoading(false);
    }
  }

  async function briefMe() {
    if (!contact) return;
    setBriefing(true);
    setError("");
    setBrief(undefined);
    try {
      setBrief(await api<Brief>(`/api/contacts/${encodeURIComponent(contact.address)}/brief`, { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not prepare a cited contact brief.");
    } finally {
      setBriefing(false);
    }
  }

  return (
    <main>
      <div className="page-intro">
        <h1>Contacts</h1>
        <p className="page-description">See the relationship history already in your Outlook mailbox, including recent threads and open commitments.</p>
      </div>
      <form className="form-panel contact-search" onSubmit={findContact}>
        <label htmlFor="contact-address">Contact email address</label>
        <input
          id="contact-address"
          type="email"
          value={address}
          onChange={event => setAddress(event.target.value)}
          placeholder="name@example.com"
          autoComplete="email"
          required
        />
        <button disabled={loading || !address.trim()}>
          {loading ? (
            <>
              <span className="button-spinner" aria-hidden="true" />
              Looking…
            </>
          ) : (
            "View contact"
          )}
        </button>
      </form>
      {error && <p className="status-message" role="alert">{error}</p>}
      {contact && (
        <section className="card contact-card stagger" aria-live="polite" key={contact.address}>
          <div className="contact-heading">
            <div>
              <h2>{contact.address}</h2>
              <p className="muted">{contact.message_count} messages · {contact.thread_count} threads</p>
            </div>
            <button type="button" onClick={briefMe} disabled={briefing || contact.message_count === 0}>
              {briefing ? (
                <>
                  <span className="button-spinner" aria-hidden="true" />
                  Preparing brief…
                </>
              ) : (
                "Brief me"
              )}
            </button>
          </div>
          <div className="contact-stats">
            <div><strong>{contact.sent_count}</strong><span>Sent to contact</span></div>
            <div><strong>{contact.received_count}</strong><span>From contact</span></div>
            <div><strong>{contact.first_date ? new Date(contact.first_date).toLocaleDateString() : "—"}</strong><span>First message</span></div>
            <div><strong>{contact.last_date ? new Date(contact.last_date).toLocaleDateString() : "—"}</strong><span>Latest message</span></div>
          </div>
          {contact.message_count === 0 ? (
            <p className="contact-empty">No stored messages match this address yet. Connect or sync the relevant Outlook mailbox and try again.</p>
          ) : (
            <>
              <div className="contact-section">
                <h3>Recent threads</h3>
                <ul className="contact-thread-list">
                  {contact.threads.map(thread => (
                    <li key={thread.thread_id}>
                      <div>
                        {thread.outlook_url
                          ? <a href={thread.outlook_url} target="_blank" rel="noopener noreferrer">{thread.subject}</a>
                          : <strong>{thread.subject}</strong>}
                        <span className="muted">{thread.sender} · {new Date(thread.last_date).toLocaleDateString()} · {thread.message_count} messages</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="contact-section">
                <h3>Open commitments</h3>
                {contact.open_commitments.length === 0 ? <p className="muted">No open commitments for this contact.</p> : (
                  <ul className="contact-thread-list">
                    {contact.open_commitments.map(item => (
                      <li key={item.id}>
                        <div>
                          <strong>{item.text}</strong>
                          <span className="muted">{item.due_date ? `Due ${item.due_date}` : item.due_text ? `Due ${item.due_text}` : "No due date"}</span>
                          {item.outlook_url && <a href={item.outlook_url} target="_blank" rel="noopener noreferrer">Source: {item.subject}</a>}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </section>
      )}
      {brief && (
        <section className="card answer-card stagger" aria-live="polite">
          <h2>Recent topics</h2>
          <p>{brief.answer || brief.explanation}</p>
          {brief.claims && brief.claims.length > 0 && (
            <ul className="answer-takeaways">
              {brief.claims.map((claim, index) => (
                <li key={`${claim.email_ids.join("-")}-${index}`}>
                  <span>{claim.text}</span>
                  <blockquote>“{claim.quote}”</blockquote>
                </li>
              ))}
            </ul>
          )}
          {brief.sources && brief.sources.length > 0 && (
            <div className="answer-sources">
              <h3>Sources</h3>
              <ul>
                {brief.sources.map(source => (
                  <li key={source.email_id}>
                    {source.outlook_url
                      ? <a href={source.outlook_url} target="_blank" rel="noopener noreferrer">{source.subject}</a>
                      : <span>{source.subject}</span>}
                    <span className="muted"> · {source.date}</span>
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
