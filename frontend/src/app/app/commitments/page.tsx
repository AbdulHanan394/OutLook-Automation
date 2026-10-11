"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { useConnectedMailbox } from "../../../lib/use-connected-mailbox";

type Commitment = {
  id: string;
  email_id: string;
  text: string;
  quote: string;
  due_date: string | null;
  due_text: string | null;
  status: "open" | "done" | "dismissed";
  subject: string;
  sender: string;
  sent_at: string;
  outlook_url: string | null;
};

type SyncStatus = { folders: string[] };

export default function CommitmentsPage() {
  const { mailboxes, mailboxId, setMailboxId, loading: mailboxesLoading, error: mailboxError } = useConnectedMailbox();
  const [items, setItems] = useState<Commitment[]>([]);
  const [filter, setFilter] = useState("open");
  const [folder, setFolder] = useState("sentitems");
  const [folders, setFolders] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    if (!mailboxId) return;
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ mailbox_id: mailboxId, status: filter });
      setItems(await api<Commitment[]>(`/api/commitments?${query.toString()}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load commitments.");
    } finally {
      setLoading(false);
    }
  }, [filter, mailboxId]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    if (!mailboxId) return;
    api<SyncStatus>(`/api/mailboxes/${mailboxId}/sync`)
      .then(status => {
        setFolders(status.folders);
        setFolder(current => status.folders.includes(current) ? current : status.folders.includes("sentitems") ? "sentitems" : status.folders[0] || "");
      })
      .catch(() => setFolders([]));
  }, [mailboxId]);

  async function extract() {
    if (!mailboxId || !folder) return;
    setWorking(true);
    setError("");
    setMessage("");
    try {
      const result = await api<{ processed: number; created: number; stopped_reason: string | null }>("/api/commitments/extract", {
        method: "POST",
        body: JSON.stringify({ mailbox_id: mailboxId, folder }),
      });
      const reason = result.stopped_reason === "daily_cap"
        ? " The daily AI limit was reached; you can continue tomorrow."
        : result.stopped_reason === "provider_spend_cap"
          ? " The AI provider has reached its spend limit."
          : result.stopped_reason === "provider_rate_limit"
            ? " The AI provider has reached its request limit."
          : result.stopped_reason === "provider_error"
            ? " The AI provider is temporarily unavailable."
            : result.stopped_reason === "invalid_output"
              ? " Some messages could not be interpreted safely."
              : "";
      setMessage(`Checked ${result.processed} messages and found ${result.created} new commitments.${reason}`);
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not extract commitments.");
    } finally {
      setWorking(false);
    }
  }

  async function updateStatus(item: Commitment, status: Commitment["status"]) {
    if (!mailboxId) return;
    setWorking(true);
    setError("");
    try {
      await api(`/api/commitments/${item.id}/status?mailbox_id=${encodeURIComponent(mailboxId)}`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update this commitment.");
    } finally {
      setWorking(false);
    }
  }

  const selectedMailbox = mailboxes.find(mailbox => mailbox.id === mailboxId);

  return (
    <main>
      <div className="page-intro">
        <h1>Commitments</h1>
        <p className="page-description">Keep track of promises and next steps from your Outlook messages, with a verified source quote for each one.</p>
      </div>
      <section className="form-panel form-grid">
        {mailboxes.length > 1 && (
          <label>
            Outlook account
            <select value={mailboxId} onChange={event => setMailboxId(event.target.value)}>
              {mailboxes.map(mailbox => <option key={mailbox.id} value={mailbox.id}>{mailbox.email}</option>)}
            </select>
          </label>
        )}
        {selectedMailbox && <p className="connected-account">Tracking commitments in <strong>{selectedMailbox.email}</strong></p>}
        <div className="button-row commitment-controls">
          <label>
            Show
            <select value={filter} onChange={event => setFilter(event.target.value)}>
              <option value="open">Open</option>
              <option value="done">Done</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All</option>
            </select>
          </label>
          {folders.length > 1 && (
            <label>
              Extract from folder
              <select value={folder} onChange={event => setFolder(event.target.value)}>
                {folders.map(name => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
          )}
          <button type="button" onClick={extract} disabled={working || loading || mailboxesLoading || !mailboxId || !selectedMailbox?.ai_consent || !folder}>
            {working ? "Checking messages…" : "Extract commitments"}
          </button>
        </div>
      </section>
      {mailboxError && <p className="status-message" role="alert">{mailboxError}</p>}
      {!mailboxesLoading && !mailboxError && mailboxes.length === 0 && (
        <p className="status-message">Connect Outlook to track commitments. <Link href="/app/settings">Go to Settings</Link></p>
      )}
      {selectedMailbox && !selectedMailbox.ai_consent && <p className="status-message">Enable AI answers for this mailbox in <Link href="/app/settings">Settings</Link> to extract commitments.</p>}
      {message && <p className="status-message" role="status">{message}</p>}
      {error && <p className="status-message" role="alert">{error}</p>}
      {loading ? <p className="muted">Loading commitments…</p> : items.length === 0 ? (
        <section className="card"><p>No {filter === "all" ? "" : `${filter} `}commitments to show yet.</p></section>
      ) : (
        <ul className="commitment-list">
          {items.map(item => (
            <li className="card commitment-card" key={item.id}>
              <div className="commitment-heading">
                <span className={`commitment-status commitment-status-${item.status}`}>{item.status}</span>
                {item.due_date
                  ? <span>Due {item.due_date}</span>
                  : item.due_text && <span>Due {item.due_text}</span>}
              </div>
              <h2>{item.text}</h2>
              <blockquote>“{item.quote}”</blockquote>
              <p className="muted">{item.sender} · {new Date(item.sent_at).toLocaleDateString()}</p>
              <div className="commitment-footer">
                <span>
                  Source:{" "}
                  {item.outlook_url
                    ? <a href={item.outlook_url} target="_blank" rel="noopener noreferrer">{item.subject}</a>
                    : item.subject}
                </span>
                <div className="button-row">
                  {item.status === "open" && (
                    <>
                      <button type="button" className="secondary-button" disabled={working} onClick={() => updateStatus(item, "done")}>Mark done</button>
                      <button type="button" className="secondary-button" disabled={working} onClick={() => updateStatus(item, "dismissed")}>Dismiss</button>
                    </>
                  )}
                  {item.status === "done" && <button type="button" className="secondary-button" disabled={working} onClick={() => updateStatus(item, "open")}>Reopen</button>}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
