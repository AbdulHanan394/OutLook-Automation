"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../../lib/api";
import MemoryPanel from "../../../components/chat/memory-panel";
type Mailbox={id:string;email:string;status:string;ai_consent:boolean}; type Sync={state:string;email_count:number;job_status?:string;job_error?:string;last_success_at?:string|null};
type FeedbackConfig={enabled:boolean;retention_days:number};
export default function Settings() {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [sync, setSync] = useState<Record<string, Sync>>({});
  const [message, setMessage] = useState("");
  const [feedbackConfig, setFeedbackConfig] = useState<FeedbackConfig>({enabled:false,retention_days:90});
  const [syncingIds, setSyncingIds] = useState<string[]>([]);

  const load = useCallback(async () => {
    const [items, feedback] = await Promise.all([
      api<Mailbox[]>("/api/mailboxes"),
      api<FeedbackConfig>("/api/search/feedback/config"),
    ]);
    setMailboxes(items);
    setFeedbackConfig(feedback);
    const statuses = await Promise.all(items.map(async x => [x.id, await api<Sync>(`/api/mailboxes/${x.id}/sync`)] as const));
    const next = Object.fromEntries(statuses);
    setSync(next);
    setSyncingIds(current => current.filter(id => next[id]?.state === "indexing"));
    return next;
  }, []);

  useEffect(() => {
    load().catch(() => setMessage("Could not load mailbox status."));
  }, [load]);

  // Poll only while a sync is actually running, then stop.
  const pollTimer = useRef<number | null>(null);
  useEffect(() => {
    if (syncingIds.length === 0) return;
    pollTimer.current = window.setTimeout(() => {
      load().catch(() => {});
    }, 2500);
    return () => { if (pollTimer.current) window.clearTimeout(pollTimer.current); };
  }, [syncingIds, load]);

  async function connect() {
    const x = await api<{ authorization_url: string }>("/api/outlook/connect", { method: "POST", body: "{}" });
    location.assign(x.authorization_url);
  }

  async function startSync(id: string) {
    setMessage("Sync started. Indexing runs in the background — this page updates as it progresses.");
    setSyncingIds(current => (current.includes(id) ? current : [...current, id]));
    try {
      await api(`/api/mailboxes/${id}/sync`, { method: "POST", body: "{}" });
    } catch {
      setMessage("Could not start the sync. Please try again.");
      setSyncingIds(current => current.filter(value => value !== id));
    }
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
      {mailboxes.map(box => {
        const status = sync[box.id];
        const indexing = syncingIds.includes(box.id);
        return (
        <section className="card mailbox-card reveal" key={box.id}>
          <h2>{box.email}</h2>
          <p>Mailbox ID: <code>{box.id}</code></p>
          <p className="muted">
            Status: {status?.state || "loading"}; {status?.email_count ?? 0} emails indexed.
          </p>
          {indexing ? (
            <div className="sync-progress" aria-live="polite" aria-busy="true">
              <div className="sync-progress-head">
                <span className="ai-orb is-quiet" aria-hidden="true"><span className="ai-orb-halo" /></span>
                <span>Indexing in progress{status?.job_status ? ` · ${status.job_status}` : ""}</span>
                <span className="progress-timer">{status?.email_count ?? 0} indexed</span>
              </div>
              <div className="progress-track" role="progressbar" aria-label={`Indexing ${box.email}`}>
                <div className="progress-fill" data-indeterminate="true" />
              </div>
            </div>
          ) : null}
          {status?.job_error && <p>Last sync error: {status.job_error}</p>}
          <div className="button-row">
            <button className="button-secondary" onClick={() => { navigator.clipboard.writeText(box.id); setMessage("Mailbox ID copied."); }}>Copy mailbox ID</button>
            <button onClick={() => startSync(box.id)} disabled={indexing}>
              {indexing ? <><span className="button-spinner" aria-hidden="true" />Syncing…</> : "Sync Outlook now"}
            </button>
            {!box.ai_consent && <button className="button-secondary" onClick={() => enableAiAnswers(box.id).catch(() => setMessage("Could not enable AI answers. Please try again."))}>Enable AI answers</button>}
          </div>
          {!box.ai_consent && <p className="muted">AI answers send relevant email excerpts to the configured AI provider after you enable them.</p>}
        </section>
        );
      })}
      {message && <p className="status-message" aria-live="polite">{message}</p>}
      <section className="card">
        <h2>AI processing</h2>
        <p className="muted">With your consent, search text can be sent to the configured AI provider to expand clues, and selected email text can be sent to the configured AI and embedding providers. Explicit search filters remain in effect; without consent, Find stays keyword-only.</p>
      </section>
      <MemoryPanel />
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
