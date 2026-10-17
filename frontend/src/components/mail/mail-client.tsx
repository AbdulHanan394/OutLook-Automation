"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  api,
  askHistory,
  askStream,
  getMessage,
  getThread,
  listFolders,
  listMailboxes,
  listMessages,
  recordAsk,
  type AskChip,
  type AskClaim,
  type AskInterpretationWire,
  type AskMode,
  type Confidence,
  type Folder,
  type Mailbox,
  type MessageDetail,
  type MessageFilter,
  type MessageRow,
  type SearchCard,
} from "../../lib/api";
import AskBar, { type AskStatus } from "./ask-bar";
import FolderPane from "./folder-pane";
import MessageListPane from "./message-list-pane";
import ReadingPane from "./reading-pane";
import ResultList, { AnswerCard, CONFIDENCE_COPY } from "./result-list";

type MobilePane = "folders" | "list" | "reading";
type SyncState = "complete" | "indexing" | "unknown";

/**
 * Chip kind -> the operator that produced it, so removing a chip can strip that
 * exact token from the query and re-run without a model call.
 */
const CHIP_OPERATORS: Record<string, string> = {
  sender: "from",
  recipient: "to",
  subject: "subject",
  folder: "folder",
  attachment: "has",
  unread: "is",
  flagged: "is",
  after: "after",
  before: "before",
};

type SyncStatus = { state: string; last_success_at: string | null };

const PAGE_SIZE = 50;

export default function MailClient() {
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [mailboxId, setMailboxId] = useState("");
  const [connectionError, setConnectionError] = useState<"no-mailbox" | "failed" | null>(null);

  const [folders, setFolders] = useState<Folder[]>([]);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [foldersCollapsed, setFoldersCollapsed] = useState(false);
  const [syncState, setSyncState] = useState<SyncState>("unknown");
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null);

  const [filter, setFilter] = useState<MessageFilter>("all");
  const [messages, setMessages] = useState<MessageRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [listLoadingMore, setListLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [thread, setThread] = useState<MessageRow[]>([]);
  const [highlightQuote, setHighlightQuote] = useState<string | null>(null);

  const [accountOpen, setAccountOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<MobilePane>("list");

  // -------------------------------------------------------------- ask state
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<AskMode>("auto");
  const [askStatus, setAskStatus] = useState<AskStatus>("idle");
  const [askActive, setAskActive] = useState(false);
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [results, setResults] = useState<SearchCard[]>([]);
  const [confidence, setConfidence] = useState<Confidence>("none");
  const [bestEmailId, setBestEmailId] = useState<string | null>(null);
  const [resultMessage, setResultMessage] = useState<string | null>(null);
  const [chips, setChips] = useState<AskChip[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [claims, setClaims] = useState<AskClaim[]>([]);
  const [answerSufficient, setAnswerSufficient] = useState(false);
  const [isAnswerMode, setIsAnswerMode] = useState(false);
  const [selectedClaim, setSelectedClaim] = useState<number | null>(null);
  const [sessionTurns, setSessionTurns] = useState<string[]>([]);
  const [conversationOpen, setConversationOpen] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [recentAsks, setRecentAsks] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  // Guard against re-fetching the same folder/filter pair.
  const loadedKey = useRef<string>("");
  // Exposed so the Ask bar and the "/" shortcut focus the same input.
  const askInputRef = useRef<HTMLInputElement | null>(null);

  // ---------------------------------------------------------------- history
  const refreshHistory = useCallback(async () => {
    if (!mailboxId) return;
    try {
      const payload = await askHistory(mailboxId);
      setSuggestions(payload.examples ?? []);
      setRecentAsks(payload.history.map((item) => item.query).slice(0, 6));
    } catch {
      setSuggestions([]);
      setRecentAsks([]);
    }
  }, [mailboxId]);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  // ---------------------------------------------------------------- mailboxes
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const all = await listMailboxes();
        const connected = all.filter((mailbox) => mailbox.status === "active");
        if (!active) return;
        setMailboxes(connected);
        setMailboxId((current) =>
          connected.some((mailbox) => mailbox.id === current) ? current : (connected[0]?.id ?? ""),
        );
        setConnectionError(connected.length === 0 ? "no-mailbox" : null);
      } catch {
        if (active) setConnectionError("failed");
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // ------------------------------------------------------- folders + sync state
  const refreshFolders = useCallback(async () => {
    if (!mailboxId) return;
    try {
      const payload = await listFolders(mailboxId);
      setFolders(payload.folders);
      setUnreadTotal(payload.unread_total);
      setFolderId((current) => {
        if (current && payload.folders.some((folder) => folder.id === current)) return current;
        const first = payload.folders.find((folder) => folder.total > 0) ?? payload.folders[0];
        return first?.id ?? null;
      });
    } catch (error) {
      setListError(error instanceof Error ? error.message : "Could not load folders.");
    }
  }, [mailboxId]);

  useEffect(() => {
    if (!mailboxId) return;
    let active = true;
    (async () => {
      try {
        const status = await api<SyncStatus>(`/api/mailboxes/${encodeURIComponent(mailboxId)}/sync`);
        if (!active) return;
        setSyncState(status.state === "indexing" ? "indexing" : "complete");
        setLastSyncedAt(status.last_success_at);
      } catch {
        if (active) setSyncState("unknown");
      }
    })();
    void refreshFolders();
    return () => {
      active = false;
    };
  }, [mailboxId, refreshFolders]);

  // ------------------------------------------------------------- message list
  const loadPage = useCallback(
    async (folder: string, activeFilter: MessageFilter, pageCursor?: string | null) => {
      if (!mailboxId) return;
      const appending = Boolean(pageCursor);
      if (appending) setListLoadingMore(true);
      else setListLoading(true);
      setListError(null);
      try {
        const page = await listMessages({
          mailboxId,
          folder,
          filter: activeFilter,
          cursor: pageCursor,
          limit: PAGE_SIZE,
        });
        setMessages((current) => (appending ? [...current, ...page.messages] : page.messages));
        setCursor(page.next_cursor);
        setHasMore(Boolean(page.next_cursor));
      } catch (error) {
        setListError(error instanceof Error ? error.message : "Could not load messages.");
      } finally {
        if (appending) setListLoadingMore(false);
        else setListLoading(false);
      }
    },
    [mailboxId],
  );

  useEffect(() => {
    if (!mailboxId || !folderId) return;
    const key = `${folderId}|${filter}`;
    if (loadedKey.current === key) return;
    loadedKey.current = key;
    setMessages([]);
    setCursor(null);
    setHasMore(false);
    void loadPage(folderId, filter);
  }, [mailboxId, folderId, filter, loadPage]);

  const loadMore = useCallback(() => {
    if (!folderId || !cursor || listLoadingMore) return;
    void loadPage(folderId, filter, cursor);
  }, [folderId, filter, cursor, listLoadingMore, loadPage]);

  // ------------------------------------------------------------------ reading
  const openMessage = useCallback(
    async (id: string) => {
      if (!mailboxId) return;
      setSelectedId(id);
      setDetailLoading(true);
      setDetailError(null);
      setHighlightQuote(null);
      setMobilePane("reading");
      try {
        const payload = await getMessage(mailboxId, id);
        setDetail(payload);
        const key = payload.conversation_id || payload.thread_key;
        if (!key) {
          setThread([]);
          return;
        }
        try {
          const conversation = await getThread(mailboxId, key);
          setThread(conversation.messages);
        } catch {
          // A thread that will not load must not block reading the message.
          setThread([]);
        }
      } catch (error) {
        setDetail(null);
        setThread([]);
        setDetailError(
          error instanceof ApiError && error.status === 404
            ? "That message is no longer available."
            : error instanceof Error
              ? error.message
              : "Could not open that message.",
        );
      } finally {
        setDetailLoading(false);
      }
    },
    [mailboxId],
  );

  const selectMessage = useCallback(
    (id: string) => {
      if (id === selectedId) return;
      void openMessage(id);
    },
    [selectedId, openMessage],
  );

  // -------------------------------------------------------------------- ask
  const runAsk = useCallback(
    async (text: string, nextMode: AskMode) => {
      if (!mailboxId || text.trim() === "") return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setAskActive(true);
      setSubmittedQuery(text);
      setStatusBusy("interpreting");
      setResults([]);
      setAnswer("");
      setClaims([]);
      setAnswerSufficient(false);
      setBestEmailId(null);
      setResultMessage(null);
      setChips([]);
      setSelectedClaim(null);
      setIsAnswerMode(false);
      setMobilePane("list");

      try {
        await askStream(
          { mailboxId, query: text, mode: nextMode },
          (event) => {
            switch (event.type) {
              case "interpretation": {
                // The SSE data payload *is* the interpretation; the server names
                // the frame, it does not nest the body under a key.
                const interpretation = event as unknown as AskInterpretationWire;
                setChips(interpretation.chips ?? []);
                setNotice(interpretation.fallback_notice ?? null);
                setIsAnswerMode(interpretation.intent === "ask");
                setStatusBusy("searching");
                break;
              }
              case "results": {
                setResults(event.results);
                setConfidence(event.confidence);
                setBestEmailId(event.best_email_id ?? null);
                setResultMessage(event.message ?? null);
                // Retrieval is done, so results can be read while the answer
                // text is still streaming.
                setStatusBusy(event.confidence === "none" ? "idle" : "writing");
                const target = event.best_email_id ?? event.results[0]?.email_id ?? null;
                if (target) {
                  const first = event.results.find((row) => row.email_id === target);
                  const claimQuote = first?.reasons?.[0]?.detail ?? null;
                  setHighlightQuote(claimQuote);
                  void openMessage(target);
                }
                break;
              }
              case "answer_delta":
                setAnswer((current) => current + event.text);
                break;
              case "citations":
                setClaims(event.claims ?? []);
                setAnswerSufficient(Boolean(event.sufficient));
                break;
              case "error":
                setNotice(event.message);
                break;
              case "done":
                setStatusBusy("idle");
                break;
            }
          },
          controller.signal,
        );
        setSessionTurns((turns) => [...turns, text]);
        try {
          await recordAsk(mailboxId, text, nextMode);
          void refreshHistory();
        } catch {
          // A failed history write must not disturb the answer.
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        setNotice(
          error instanceof ApiError ? error.message : "The assistant is unavailable right now.",
        );
      } finally {
        setStatusBusy("idle");
      }
    },
    // setStatusBusy is a useCallback with stable identity, so it is safe to omit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mailboxId, openMessage, refreshHistory],
  );

  const setStatusBusy = useCallback((next: AskStatus) => setAskStatus(next), []);

  const clearAsk = useCallback(() => {
    abortRef.current?.abort();
    setAskActive(false);
    setMobilePane("list");
    setQuery("");
    setResults([]);
    setChips([]);
    setAnswer("");
    setClaims([]);
    setAnswerSufficient(false);
    setNotice(null);
    setSubmittedQuery("");
    setSelectedClaim(null);
    setIsAnswerMode(false);
    setConfidence("none");
  }, []);

  const removeChip = useCallback(
    (chip: AskChip) => {
      // Rewrite the query by removing that one operator, then re-run. The
      // re-run needs no interpretation call: what is left is either pure
      // operators (fully deterministic) or the same free text as before.
      const operator = CHIP_OPERATORS[chip.kind];
      let next = query;
      if (operator) {
        const pattern = new RegExp(
          `(^|\\s)${operator}:\\S+\\s?`,
          "gi",
        );
        next = query.replace(pattern, " ").replace(/\s{2,}/g, " ").trim();
      } else if (chip.kind === "clue") {
        // A guessed clue is a soft ranking signal, not part of the query text.
        setChips((current) => current.filter((item) => item !== chip));
        return;
      }
      setQuery(next);
      void runAsk(next, mode);
    },
    [query, mode, runAsk],
  );

  // ----------------------------------------------------------------- keyboard
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        !!target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);

      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        askInputRef.current?.focus();
        return;
      }
      if (event.key === "/" && !typing) {
        event.preventDefault();
        askInputRef.current?.focus();
        return;
      }
      if (typing) {
        if (event.key === "Escape") (target as HTMLInputElement).blur();
        return;
      }
      if (event.key === "Escape") {
        if (askActive) {
          // Esc in a search returns to the folder view the reader came from.
          clearAsk();
          return;
        }
        if (selectedId) {
          setSelectedId(null);
          setDetail(null);
          setThread([]);
        }
        setMobilePane("list");
        return;
      }
      const forward = event.key === "j" || event.key === "ArrowDown";
      const backward = event.key === "k" || event.key === "ArrowUp";
      if ((forward || backward) && messages.length > 0) {
        event.preventDefault();
        const index = messages.findIndex((row) => row.id === selectedId);
        const delta = forward ? 1 : -1;
        const next = index === -1 ? 0 : Math.min(messages.length - 1, Math.max(0, index + delta));
        void openMessage(messages[next].id);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [messages, selectedId, openMessage, askActive, clearAsk]);

  const selectedMailbox = useMemo(
    () => mailboxes.find((mailbox) => mailbox.id === mailboxId) ?? null,
    [mailboxes, mailboxId],
  );
  const bestResult =
    results.find((result) => result.email_id === bestEmailId) ?? results[0] ?? null;

  if (connectionError === "no-mailbox") {
    return (
      <main className="mail-page">
        <div className="pane-empty connect-prompt">
          <h1>No mailbox connected</h1>
          <p>Connect a read-only Outlook mailbox to browse and search your mail.</p>
          <a className="button-secondary" href="/app/settings">
            Connect a mailbox
          </a>
        </div>
      </main>
    );
  }

  if (connectionError === "failed") {
    return (
      <main className="mail-page">
        <div className="pane-empty" role="alert">
          <p>We could not reach your account. Check your connection and try again.</p>
          <button
            type="button"
            className="button-secondary"
            onClick={() => window.location.reload()}
          >
            Try again
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="mail-page" data-pane={mobilePane}>
      <h1 className="sr-only">Mail</h1>
      <AskBar
        value={query}
        onChange={setQuery}
        onSubmit={() => void runAsk(query, mode)}
        onClear={clearAsk}
        mode={mode}
        onModeChange={setMode}
        status={askStatus}
        chips={chips}
        onRemoveChip={removeChip}
        notice={notice}
        active={askActive}
        suggestions={suggestions}
        recentAsks={recentAsks}
        onSuggestion={(value) => {
          setQuery(value);
          void runAsk(value, mode);
        }}
        onNewSearch={() => {
          setSessionTurns([]);
          clearAsk();
        }}
        conversationOpen={conversationOpen}
        onToggleConversation={() => setConversationOpen((value) => !value)}
        sessionTurns={sessionTurns}
        inputRef={askInputRef}
        disabled={mailboxes.length === 0}
        disabledReason="Connect a mailbox to ask"
      />

      {askActive ? (
        <div className="search-banner" role="region" aria-label="Search results">
          <p>
            Showing results for: <q>{submittedQuery}</q>
          </p>
          {!isAnswerMode && (results.length > 0 || resultMessage !== null) ? (
            <>
              <p
                className="result-confidence search-confidence"
                data-confidence={confidence}
                aria-live="polite"
              >
                {CONFIDENCE_COPY[confidence]}
              </p>
              {bestResult && bestResult.reasons.length > 0 ? (
                <div className="search-reasons" role="group" aria-label="Why this email matched">
                  {bestResult.reasons.map((reason, index) => (
                    <span className="reason-chip" key={`${reason.type}-${index}`}>
                      <span className="reason-type">{reason.type}</span>
                      {reason.detail}
                    </span>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
          <button type="button" className="text-button" onClick={clearAsk}>
            <span aria-hidden="true">×</span> Clear
          </button>
        </div>
      ) : null}

      <div className="mail-panes">
        <FolderPane
          folders={folders}
          selected={folderId}
          unreadTotal={unreadTotal}
          syncState={syncState}
          lastSyncedAt={lastSyncedAt}
          onSelect={(id) => {
            setFolderId(id);
            loadedKey.current = "";
            setMobilePane("list");
            clearAsk();
          }}
          onToggle={() => setFoldersCollapsed((value) => !value)}
          collapsed={foldersCollapsed}
        />

        {askActive ? (
          <section className="list-pane" aria-label="Search results" aria-busy={askStatus !== "idle"}>
            {isAnswerMode ? (
              <div className="answer-scroll">
                <AnswerCard
                  answer={answer}
                  claims={claims}
                  sufficient={answerSufficient}
                  hasResults={results.length > 0}
                  selectedClaim={selectedClaim}
                  onCitation={(index) => {
                    setSelectedClaim(index);
                    const claim = claims[index];
                    const firstEmail = claim?.email_ids[0];
                    if (firstEmail) {
                      setHighlightQuote(claim.quote);
                      void openMessage(firstEmail);
                    }
                  }}
                />
              </div>
            ) : null}
            <div className="list-scroll">
              <ResultList
                results={results}
                confidence={confidence}
                bestEmailId={bestEmailId}
                selectedId={selectedId}
                message={resultMessage}
                onSelect={(id) => {
                  setSelectedClaim(null);
                  void openMessage(id);
                }}
                onReword={() => askInputRef.current?.focus()}
              />
            </div>
          </section>
        ) : (
          <MessageListPane
            messages={messages}
            selectedId={selectedId}
            filter={filter}
            loading={listLoading}
            loadingMore={listLoadingMore}
            hasMore={hasMore}
            error={listError}
            sentFolder={(folderId ?? "").toLowerCase() === "sentitems"}
            resultMode={false}
            emptyMessage={
              syncState === "indexing"
                ? "Sync in progress: results may be incomplete."
                : folderId
                  ? null
                  : "No folders have been synced yet."
            }
            onFilter={setFilter}
            onSelect={selectMessage}
            onLoadMore={loadMore}
          />
        )}

        <ReadingPane
          detail={detail}
          loading={detailLoading}
          error={detailError}
          mailboxId={mailboxId || null}
          thread={thread}
          highlightQuote={highlightQuote}
          onOpenThreadMessage={(id) => void openMessage(id)}
          onRetry={() => selectedId && void openMessage(selectedId)}
        />
      </div>

      {/* Mobile single-pane navigation; hidden on desktop by CSS. */}
      <div className="mobile-bar">
        <button
          type="button"
          className="text-button"
          onClick={() => setMobilePane(mobilePane === "reading" ? "list" : "folders")}
        >
          <span aria-hidden="true">←</span> Back
        </button>
        <span className="mobile-pane-label">
          {mobilePane === "folders" ? "Folders" : mobilePane === "list" ? (folderId ?? "") : "Message"}
        </span>
        {mobilePane === "list" ? (
          <button type="button" className="text-button" onClick={() => setMobilePane("folders")}>
            Folders
          </button>
        ) : null}
      </div>

      {/* Ask bar lands in OL2; the ref above already points at its input. */}
      <div className="account-menu-wrapper" hidden>
        <button type="button" onClick={() => setAccountOpen((value) => !value)}>
          {selectedMailbox?.email ?? "Account"}
        </button>
        {accountOpen ? <div role="menu" /> : null}
      </div>
    </main>
  );
}