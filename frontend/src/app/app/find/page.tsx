"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import SearchProgress, { ProgressStep } from "../../../components/search-progress";
import { api } from "../../../lib/api";
import { useConnectedMailbox } from "../../../lib/use-connected-mailbox";

type Reason = { type: "sender" | "date" | "phrase" | "subject" | "semantic" | "attachment"; detail: string };
type Card = { email_id: string; subject: string; sender: string; received_at: string; preview: string; body?: string; outlook_url?: string; why: string[]; reasons: Reason[]; thread_count?: number };
type Result = { confidence: string; search_id?: string; feedback_enabled?: boolean; interpretation_mode?: "keyword" | "ai"; best_match?: Card; alternatives?: Card[]; message?: string };
type SavedSearch = { id: string; name: string; query: string; filters: Record<string, unknown> };
type PinnedEmail = { email_id: string; subject: string; sender: string; received_at: string; preview: string; outlook_url?: string };
type ThreadMessage = Omit<PinnedEmail, "preview"> & { preview: string | null };
type ThreadSummary = {
  sufficient_evidence: boolean;
  answer: string | null;
  explanation?: string;
  claims?: { text: string; email_ids: string[]; quote: string }[];
  closest_emails?: { email_id: string; subject: string; outlook_url?: string }[];
};

const FILE_TYPES = ["pdf", "docx", "xlsx", "txt", "csv"] as const;

const SEARCH_STEPS: ProgressStep[] = [
  { id: "parse", label: "Reading your query", detail: "Pulling out people, phrases and dates.", at: 0 },
  { id: "interpret", label: "AI is expanding your clues", detail: "Turning what you remember into search terms.", at: 380, ai: true },
  { id: "retrieve", label: "Scanning your mailbox", detail: "Searching subject, body and attachments.", at: 1750 },
  { id: "rank", label: "Ranking the closest matches", detail: "Cross-checking every retriever and scoring confidence.", at: 3100 },
];

const REFINE_STEPS: ProgressStep[] = [
  { id: "read", label: "Reading your change", detail: "Working out what needs to change in the ranking.", at: 0 },
  { id: "reweight", label: "Re-weighting the signals", detail: "Applying your hint to sender, date and phrase scores.", at: 420 },
  { id: "rescore", label: "Scoring again", detail: "Cross-checking every retriever and scoring confidence.", at: 1450 },
];

function SummaryPanel({ summary }: { summary: ThreadSummary }) {
  return (
    <div className="thread-summary">
      {summary.sufficient_evidence && summary.answer
        ? <p>{summary.answer}</p>
        : <p className="muted">{summary.explanation || "There is not enough verified evidence to summarize this email."}</p>}
      {summary.claims?.map((claim, index) => (
        <blockquote key={`${claim.quote}-${index}`}>
          <span>{claim.text}</span>
          <q>{claim.quote}</q>
          {claim.email_ids.map(sourceId => {
            const source = summary.closest_emails?.find(email => email.email_id === sourceId);
            return source?.outlook_url
              ? <a key={sourceId} href={source.outlook_url} target="_blank" rel="noopener noreferrer">{source.subject || "Open cited email"}</a>
              : null;
          })}
        </blockquote>
      ))}
    </div>
  );
}

function EmailCard({ item, onReject, refining, pinned, onPin, searchId, feedbackEnabled, onFeedback, threadMessages, expandedThread, onToggleThread, summaries, summarizing, onSummarize }: { item: Card; onReject: (emailId: string) => void; refining: boolean; pinned: boolean; onPin: (emailId: string) => void; searchId?: string; feedbackEnabled?: boolean; onFeedback: (emailId: string, rating: "helpful" | "not_helpful") => void; threadMessages: ThreadMessage[]; expandedThread: boolean; onToggleThread: (emailId: string) => void; summaries: Partial<Record<"email" | "thread", ThreadSummary>>; summarizing: Partial<Record<"email" | "thread", boolean>>; onSummarize: (emailId: string, scope: "email" | "thread") => void }) {
  return (
    <article className="card result-card">
      <h2>{item.subject || "(no subject)"}</h2>
      <div className="result-meta">
        <span>{item.sender}</span>
        <span>{new Date(item.received_at).toLocaleDateString()}</span>
      </div>
      <div className="result-body">
        <h3>Email</h3>
        <p className="result-preview">
          {item.body || item.preview || "(This email has no readable body.)"}
        </p>
      </div>
      {item.reasons.length > 0 && (
        <ul className="reason-chips" aria-label="Why this email matched">
          {item.reasons.map((reason, index) => (
            <li className={`reason-chip reason-${reason.type}`} key={`${reason.type}-${index}`}>
              <span className="reason-type">{reason.type}</span>
              <span>{reason.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {item.outlook_url && <a className="outlook-link" href={item.outlook_url} target="_blank" rel="noopener noreferrer">Open in Outlook <span aria-hidden="true">↗</span></a>}
      <div className="result-actions">
        <button className="button-secondary" type="button" onClick={() => onSummarize(item.email_id, "email")} disabled={summarizing.email}>
          {summarizing.email ? "Summarizing…" : "Summarize email"}
        </button>
        {(item.thread_count || 1) > 1 && (
          <button className="button-secondary" type="button" onClick={() => onSummarize(item.email_id, "thread")} disabled={summarizing.thread}>
            {summarizing.thread ? "Summarizing…" : "Summarize thread"}
          </button>
        )}
      </div>
      {summaries.email && <SummaryPanel summary={summaries.email} />}
      {summaries.thread && <SummaryPanel summary={summaries.thread} />}
      {(item.thread_count || 1) > 1 && (
        <>
          <button className="text-button" type="button" onClick={() => onToggleThread(item.email_id)}>
            {expandedThread ? "Hide related messages" : `Show ${item.thread_count} related messages`}
          </button>
          {expandedThread && (
            <div className="thread-expansion">
              <ul>
                {threadMessages.map(message => (
                  <li key={message.email_id}>
                    <strong>{message.subject || "(no subject)"}</strong>
                    <span className="muted">{message.sender} · {new Date(message.received_at).toLocaleDateString()}</span>
                    {message.preview && <span>{message.preview}</span>}
                    {message.outlook_url && <a href={message.outlook_url} target="_blank" rel="noopener noreferrer">Open message in Outlook</a>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <button className="button-secondary" type="button" onClick={() => onPin(item.email_id)} disabled={pinned}>
        {pinned ? "Pinned" : "Pin email"}
      </button>
      {feedbackEnabled && searchId && (
        <div className="result-feedback" aria-label="Rate this search result">
          <button className="text-button" type="button" onClick={() => onFeedback(item.email_id, "helpful")}>Helpful</button>
          <button className="text-button" type="button" onClick={() => onFeedback(item.email_id, "not_helpful")}>Not helpful</button>
        </div>
      )}
      <button className="button-secondary" type="button" onClick={() => onReject(item.email_id)} disabled={refining}>
        {refining ? "Refining…" : "Not this one"}
      </button>
    </article>
  );
}

export default function Find() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<Result>();
  const [searching, setSearching] = useState(false);
  const [refining, setRefining] = useState(false);
  const [resultRun, setResultRun] = useState(0);
  const [busyLabel, setBusyLabel] = useState("");
  const [refinementHint, setRefinementHint] = useState("");
  const [refinementError, setRefinementError] = useState("");
  const [savedSearches, setSavedSearches] = useState<SavedSearch[]>([]);
  const [pinnedEmails, setPinnedEmails] = useState<PinnedEmail[]>([]);
  const [savedName, setSavedName] = useState("");
  const [threadMessages, setThreadMessages] = useState<Record<string, ThreadMessage[]>>({});
  const [expandedThreadIds, setExpandedThreadIds] = useState<string[]>([]);
  const [threadSummaries, setThreadSummaries] = useState<Record<string, Partial<Record<"email" | "thread", ThreadSummary>>>>({});
  const [summarizingThreadIds, setSummarizingThreadIds] = useState<string[]>([]);
  const [fileTypes, setFileTypes] = useState<string[]>([]);
  const { mailboxes, mailboxId, setMailboxId, loading: mailboxesLoading, error: mailboxError } = useConnectedMailbox();

  async function loadSavedItems() {
    if (!mailboxId) return;
    const [searches, pins] = await Promise.all([
      api<SavedSearch[]>(`/api/saved-searches?mailbox_id=${mailboxId}`),
      api<PinnedEmail[]>(`/api/pinned-emails?mailbox_id=${mailboxId}`),
    ]);
    setSavedSearches(searches);
    setPinnedEmails(pins);
  }

  useEffect(() => {
    if (!mailboxId) {
      setSavedSearches([]);
      setPinnedEmails([]);
      return;
    }
    loadSavedItems().catch(() => setRefinementError("Could not load saved searches and pinned emails."));
    // Deliberately keyed on mailboxId alone: loadSavedItems is recreated each
    // render and reads mailboxId from the same closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mailboxId]);

  async function runSearch(searchQuery: string, filters: Record<string, unknown> = {}) {
    setSearching(true);
    setResult(undefined);
    setRefinementHint("");
    setRefinementError("");
    try {
      const next = await api<Result>("/api/search/email", { method: "POST", body: JSON.stringify({ mailbox_id: mailboxId, query: searchQuery, filters }) });
      setResult(next);
      setResultRun(current => current + 1);
      setQuery(searchQuery);
      setFileTypes(Array.isArray(filters.file_types) ? filters.file_types.filter((item): item is string => typeof item === "string") : []);
      setRefinementHint("");
      setRefinementError("");
    } finally {
      setSearching(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    try { await runSearch(query, fileTypes.length ? { file_types: fileTypes } : {}); }
    catch { setRefinementError("Search failed. Please try again."); }
  }

  async function saveCurrentSearch() {
    try {
      const saved = await api<SavedSearch>("/api/saved-searches", {
        method: "POST",
        body: JSON.stringify({ mailbox_id: mailboxId, name: savedName, query, filters: fileTypes.length ? { file_types: fileTypes } : {} }),
      });
      setSavedSearches(current => [saved, ...current]);
      setSavedName("");
      setRefinementError("");
    } catch {
      setRefinementError("Could not save this search. Enter a name and try again.");
    }
  }

  async function pinEmail(emailId: string) {
    try {
      await api("/api/pinned-emails", { method: "POST", body: JSON.stringify({ mailbox_id: mailboxId, email_id: emailId }) });
      await loadSavedItems();
      setRefinementError("");
    } catch {
      setRefinementError("Could not pin this email.");
    }
  }

  async function unpinEmail(emailId: string) {
    try {
      await api(`/api/pinned-emails/${emailId}`, { method: "DELETE" });
      setPinnedEmails(current => current.filter(item => item.email_id !== emailId));
    } catch {
      setRefinementError("Could not remove the pinned email.");
    }
  }

  async function deleteSavedSearch(id: string) {
    try {
      await api(`/api/saved-searches/${id}`, { method: "DELETE" });
      setSavedSearches(current => current.filter(item => item.id !== id));
    } catch {
      setRefinementError("Could not delete the saved search.");
    }
  }

  async function rateResult(emailId: string, rating: "helpful" | "not_helpful") {
    if (!result?.search_id) return;
    try {
      await api(`/api/search/${result.search_id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ email_id: emailId, rating }),
      });
      setRefinementError("Thanks — your feedback will help improve future ranking.");
    } catch {
      setRefinementError("Could not save search feedback.");
    }
  }

  async function toggleThread(emailId: string) {
    if (expandedThreadIds.includes(emailId)) {
      setExpandedThreadIds(current => current.filter(id => id !== emailId));
      return;
    }
    try {
      const messages = threadMessages[emailId] || await api<ThreadMessage[]>(`/api/emails/${emailId}/thread`);
      setThreadMessages(current => ({ ...current, [emailId]: messages }));
      setExpandedThreadIds(current => [...current, emailId]);
    } catch {
      setRefinementError("Could not load related messages.");
    }
  }

  async function summarizeThread(emailId: string, scope: "email" | "thread") {
    const key = `${emailId}:${scope}`;
    setSummarizingThreadIds(current => [...current, key]);
    try {
      const summary = await api<ThreadSummary>(`/api/emails/${emailId}/thread-summary`, {
        method: "POST",
        body: JSON.stringify({ scope }),
      });
      setThreadSummaries(current => ({
        ...current,
        [emailId]: { ...current[emailId], [scope]: summary },
      }));
      setRefinementError("");
    } catch {
      setRefinementError(`Could not summarize this ${scope}. Check AI consent and try again.`);
    } finally {
      setSummarizingThreadIds(current => current.filter(id => id !== key));
    }
  }

  async function rejectResult(emailId: string) {
    if (!result?.search_id) return;
    setRefining(true);
    setBusyLabel("Re-ranking without that email");
    setRefinementError("");
    try {
      const next = await api<Result>(`/api/search/${result.search_id}/refine`, {
        method: "POST",
        body: JSON.stringify({ rejected_email_ids: [emailId], hint: refinementHint || null }),
      });
      setResult(next);
      setResultRun(current => current + 1);
    } catch (error) {
      setRefinementError(error instanceof Error ? error.message : "Could not refine these results.");
    } finally {
      setRefining(false);
      setBusyLabel("");
    }
  }

  async function applyRefinement() {
    if (!result?.search_id || !refinementHint.trim()) return;
    const reference = result.best_match || result.alternatives?.[0];
    if (!reference) return;
    setRefining(true);
    setBusyLabel("Applying your change and searching again");
    setRefinementError("");
    try {
      const next = await api<Result>(`/api/search/${result.search_id}/refine`, {
        method: "POST",
        body: JSON.stringify({
          rejected_email_ids: [],
          hint: refinementHint.trim(),
          reference_email_id: reference.email_id,
        }),
      });
      setResult(next);
      setResultRun(current => current + 1);
      setRefinementHint("");
    } catch (error) {
      setRefinementError(error instanceof Error ? error.message : "Could not apply this change.");
    } finally {
      setRefining(false);
      setBusyLabel("");
    }
  }
  return (
    <main>
      <div className="page-intro">
        <h1>Find an email</h1>
        <p className="page-description">Search words you remember to find the message in your connected Outlook mailbox.</p>
      </div>
      <form className="form-panel form-grid find-form" onSubmit={submit}>
        {mailboxes.length > 1 && (
          <label>
            Outlook account
            <select value={mailboxId} onChange={event => setMailboxId(event.target.value)} required>
              {mailboxes.map(mailbox => <option key={mailbox.id} value={mailbox.id}>{mailbox.email}</option>)}
            </select>
          </label>
        )}
        {mailboxes.length === 1 && <p className="connected-account">Searching in <strong>{mailboxes[0].email}</strong></p>}
        <label>
          What do you remember?
          <input aria-label="Search emails" value={query} onChange={event => setQuery(event.target.value)} required />
        </label>
        <fieldset className="attachment-filters">
          <legend>Search inside attachments</legend>
          <div className="attachment-filter-options">
            {FILE_TYPES.map(fileType => (
              <label key={fileType}>
                <input
                  type="checkbox"
                  checked={fileTypes.includes(fileType)}
                  onChange={event => setFileTypes(current => event.target.checked
                    ? [...current, fileType]
                    : current.filter(value => value !== fileType))}
                />
                {fileType.toUpperCase()}
              </label>
            ))}
          </div>
          <span className="muted">Only matching emails with one of the selected file types will be shown. Leave all unchecked to include supported attachments in regular searches.</span>
        </fieldset>
        <div className="button-row">
          <button className="find-submit" disabled={searching || refining || mailboxesLoading || !mailboxId}>
            {searching ? (
              <>
                <span className="button-spinner" aria-hidden="true" />
                Searching your mailbox…
              </>
            ) : refining ? (
              <>
                <span className="button-spinner" aria-hidden="true" />
                Refining…
              </>
            ) : (
              <>Find email</>
            )}
          </button>
        </div>
      </form>
      {!!mailboxId && (
        <section className="saved-search-panel">
          <form className="button-row" onSubmit={event => { event.preventDefault(); void saveCurrentSearch(); }}>
            <label>
              Save this search
              <input value={savedName} onChange={event => setSavedName(event.target.value)} placeholder="Name this search" maxLength={80} required />
            </label>
            <button className="button-secondary" type="submit" disabled={!query.trim()}>Save</button>
          </form>
          {savedSearches.length > 0 && (
            <div>
              <h2>Saved searches</h2>
              <ul className="saved-search-list">
                {savedSearches.map(saved => (
                  <li key={saved.id}>
                    <button className="button-secondary" type="button" onClick={() => runSearch(saved.query, saved.filters).catch(() => setRefinementError("Could not run this saved search."))}>{saved.name}</button>
                    <button className="text-button" type="button" onClick={() => deleteSavedSearch(saved.id)}>Delete</button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {pinnedEmails.length > 0 && (
            <div>
              <h2>Pinned emails</h2>
              <ul className="pinned-email-list">
                {pinnedEmails.map(item => (
                  <li key={item.email_id}>
                    {item.outlook_url
                      ? <a href={item.outlook_url} target="_blank" rel="noopener noreferrer">{item.subject || "(no subject)"}</a>
                      : <span>{item.subject || "(no subject)"}</span>}
                    <span className="muted">{item.sender} · {new Date(item.received_at).toLocaleDateString()}</span>
                    <button className="text-button" type="button" onClick={() => unpinEmail(item.email_id)}>Unpin</button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
      {mailboxError && <p className="status-message" role="alert">{mailboxError}</p>}
      {!mailboxesLoading && !mailboxError && mailboxes.length === 0 && (
        <p className="status-message">Connect Outlook to start searching. <Link href="/app/settings">Go to Settings</Link></p>
      )}
      <SearchProgress
        steps={refining ? REFINE_STEPS : SEARCH_STEPS}
        running={searching || refining}
        heading={refining ? busyLabel || "Refining your results" : "Searching your mailbox"}
        skeleton={refining ? 0 : 2}
      />
      <section className="result-list" aria-live="polite" key={resultRun}>
        {result?.interpretation_mode && (
          <p className="muted">
            {result.interpretation_mode === "ai"
              ? "AI-assisted clue matching is on; your search filters remain in effect."
              : "Keyword-only search is on for this mailbox."}
          </p>
        )}
        {result?.search_id && (result.confidence === "high" || result.confidence === "low") && (
          <div className="refinement-panel">
            <div>
              <h2>Refine these results</h2>
              <p className="muted">Add a detail and select “Apply change” to search again. Relative dates use the top result as a reference; “Not this one” excludes a result.</p>
            </div>
            <label>
              What should I change?
              <input value={refinementHint} onChange={event => setRefinementHint(event.target.value)} placeholder="e.g. older than this, from Alex, about the budget" />
            </label>
            <button className="button-secondary" type="button" onClick={applyRefinement} disabled={refining || searching || !refinementHint.trim()}>
              {refining ? "Applying…" : "Apply change"}
            </button>
          </div>
        )}
        {refinementError && <p className="status-message" role="alert">{refinementError}</p>}
        {result?.confidence === "high" && result.best_match && <EmailCard item={result.best_match} onReject={rejectResult} refining={refining} pinned={pinnedEmails.some(email => email.email_id === result.best_match?.email_id)} onPin={pinEmail} searchId={result.search_id} feedbackEnabled={result.feedback_enabled} onFeedback={rateResult} threadMessages={threadMessages[result.best_match.email_id] || []} expandedThread={expandedThreadIds.includes(result.best_match.email_id)} onToggleThread={toggleThread} summaries={threadSummaries[result.best_match.email_id] || {}} summarizing={{ email: summarizingThreadIds.includes(`${result.best_match.email_id}:email`), thread: summarizingThreadIds.includes(`${result.best_match.email_id}:thread`) }} onSummarize={summarizeThread} />}
        {result?.confidence === "low" && (
          <>
            <p className="muted">Several emails look similar. Add a sender or date to narrow it down.</p>
            {result.alternatives?.map(item => <EmailCard key={item.email_id} item={item} onReject={rejectResult} refining={refining} pinned={pinnedEmails.some(email => email.email_id === item.email_id)} onPin={pinEmail} searchId={result.search_id} feedbackEnabled={result.feedback_enabled} onFeedback={rateResult} threadMessages={threadMessages[item.email_id] || []} expandedThread={expandedThreadIds.includes(item.email_id)} onToggleThread={toggleThread} summaries={threadSummaries[item.email_id] || {}} summarizing={{ email: summarizingThreadIds.includes(`${item.email_id}:email`), thread: summarizingThreadIds.includes(`${item.email_id}:thread`) }} onSummarize={summarizeThread} />)}
          </>
        )}
        {result?.confidence === "none" && <p className="card">{result.message || "No reliable match found. Try different words or check indexing progress."}</p>}
      </section>
    </main>
  );
}
