"use client";
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
type Mailbox={id:string;email:string;status:string;ai_consent:boolean}; type Sync={state:string;email_count:number;job_status?:string;job_error?:string};
type FeedbackConfig={enabled:boolean;retention_days:number};
export default function Settings() {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [sync, setSync] = useState<Record<string, Sync>>({});
  const [message, setMessage] = useState("");
  const [feedbackConfig, setFeedbackConfig] = useState<FeedbackConfig>({enabled:false,retention_days:90});

  async function load() {
    const [items, feedback] = await Promise.all([
      api<Mailbox[]>("/api/mailboxes"),
      api<FeedbackConfig>("/api/search/feedback/config"),
    ]);
    setMailboxes(items);
    setFeedbackConfig(feedback);
    const statuses = await Promise.all(items.map(async x => [x.id, await api<Sync>(`/api/mailboxes/${x.id}/sync`)] as const));
    setSync(Object.fromEntries(statuses));
  }

  useEffect(() => {
    load().catch(() => setMessage("Could not load mailbox status."));
  }, []);

  async function connect() {
    const x = await api<{ authorization_url: string }>("/api/outlook/connect", { method: "POST", body: "{}" });
    location.assign(x.authorization_url);
  }

  async function startSync(id: string) {
    setMessage("Sync started. This page will refresh shortly.");
    await api(`/api/mailboxes/${id}/sync`, { method: "POST", body: "{}" });
    setTimeout(() => load().catch(() => {}), 3000);
  }

  async function enableAiAnswers(id: string) {
    await api(`/api/mailboxes/${id}/consent/ai`, { method: "POST", body: "{}" });
    setMailboxes(current => current.map(box => box.id === id ? { ...box, ai_consent: true } : box));
    setMessage("AI answers enabled for this mailbox.");
  }

  async function clearFeedbackHistory() {
    try {
      const result = await api<{deleted:number}>("/api/search/feedback/history", {method:"DELETE"});
      setMessage(`Cleared ${result.deleted} search feedback event${result.deleted === 1 ? "" : "s"}.`);
    } catch {
      setMessage("Could not clear search feedback history. Please try again.");
    }
  }

  return (
    <main>
      <div className="page-intro">
        <h1>Settings</h1>
        <p className="page-description">Manage your Outlook connection, mailbox indexing, and AI preferences.</p>
      </div>
      <section className="card">
        <h2>Connect Outlook</h2>
        <p className="muted">Inbox and Sent Items are indexed for the configured lookback period. Attachments are not indexed.</p>
        <button onClick={connect}>Connect Outlook</button>
      </section>
      {mailboxes.map(box => (
        <section className="card mailbox-card" key={box.id}>
          <h2>{box.email}</h2>
          <p>Mailbox ID: <code>{box.id}</code></p>
          <p className="muted">Status: {sync[box.id]?.state || "loading"}; {sync[box.id]?.email_count ?? 0} emails indexed.</p>
          {sync[box.id]?.job_error && <p>Last sync error: {sync[box.id].job_error}</p>}
          <div className="button-row">
            <button className="button-secondary" onClick={() => { navigator.clipboard.writeText(box.id); setMessage("Mailbox ID copied."); }}>Copy mailbox ID</button>
            <button onClick={() => startSync(box.id)}>Sync Outlook now</button>
            {!box.ai_consent && <button className="button-secondary" onClick={() => enableAiAnswers(box.id).catch(() => setMessage("Could not enable AI answers. Please try again."))}>Enable AI answers</button>}
          </div>
          {!box.ai_consent && <p className="muted">AI answers send relevant email excerpts to the configured AI provider after you enable them.</p>}
        </section>
      ))}
      {message && <p className="status-message" aria-live="polite">{message}</p>}
      <section className="card">
        <h2>AI processing</h2>
        <p className="muted">With your consent, search text can be sent to the configured AI provider to expand clues, and selected email text can be sent to the configured AI and embedding providers. Explicit search filters remain in effect; without consent, Find stays keyword-only.</p>
      </section>
      {feedbackConfig.enabled && (
        <section className="card">
          <h2>Search ranking feedback</h2>
          <p className="muted">Helpful/not-helpful ratings may adjust tie-breaking for your own search results. Events are automatically removed after {feedbackConfig.retention_days} days.</p>
          <button className="button-secondary" onClick={clearFeedbackHistory}>Clear my ranking feedback</button>
        </section>
      )}
    </main>
  );
}
