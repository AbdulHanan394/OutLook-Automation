"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import ChatPanel, { StreamingTurn } from "../../../components/chat/chat-panel";
import ChatSidebar, { useChatHistory } from "../../../components/chat/chat-sidebar";
import ReadingPane from "../../../components/mail/reading-pane";
import { MessageDetail, MessageRow, getMessage, getThread } from "../../../lib/api";
import {
  ChatCitation,
  ChatMessage,
  ChatSummary,
  ChatTurnEvent,
  createChat,
  fetchSuggestions,
  getChat,
  sendChatMessage,
  sendTemporaryMessage,
} from "../../../lib/chats";
import { useConnectedMailbox } from "../../../lib/use-connected-mailbox";

/**
 * The conversational Ask page.
 *
 * Three panes on the `.mail-page` grid so each scrolls independently: chat
 * history, the transcript, and the reading pane. The reading pane is the same
 * component the mail shell uses, so an email cited in a chat opens exactly the
 * way it opens in the mailbox.
 */
export default function Ask() {
  const { chats, setChats, loading, error: historyError, refresh } = useChatHistory();
  const { mailboxes, mailboxId, setMailboxId, loading: mailboxesLoading, error: mailboxError } =
    useConnectedMailbox();

  const [activeId, setActiveId] = useState<string | null>(null);
  const [savedMessages, setSavedMessages] = useState<ChatMessage[]>([]);
  const [chatMailboxId, setChatMailboxId] = useState<string | null>(null);
  const [temporary, setTemporary] = useState(false);
  // A temporary chat has no id, so its transcript exists only here and is
  // discarded when the chat is closed.
  const [temporaryMessages, setTemporaryMessages] = useState<ChatMessage[]>([]);

  const [streaming, setStreaming] = useState<StreamingTurn | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);

  const [openEmailId, setOpenEmailId] = useState<string | null>(null);
  const [messageDetail, setMessageDetail] = useState<MessageDetail | null>(null);
  const [messageThread, setMessageThread] = useState<MessageRow[]>([]);
  const [messageLoading, setMessageLoading] = useState(false);
  const [messageError, setMessageError] = useState<string | null>(null);

  const selectedMailbox = mailboxes.find((mailbox) => mailbox.id === mailboxId);
  const consentMissing = Boolean(selectedMailbox) && !selectedMailbox?.ai_consent;
  const noMailbox = !mailboxesLoading && !mailboxError && mailboxes.length === 0;

  const blockedReason = noMailbox
    ? "Connect Outlook to ask questions about your email."
    : consentMissing
      ? "Enable AI answers for this mailbox in Settings to continue."
      : null;

  // Load the active chat.
  useEffect(() => {
    if (!activeId) {
      setSavedMessages([]);
      setChatMailboxId(null);
      return;
    }
    let cancelled = false;
    getChat(activeId)
      .then((found) => {
        if (cancelled) return;
        setSavedMessages(found.messages);
        setChatMailboxId(found.mailbox_id);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setPageError(cause instanceof Error ? cause.message : "Could not open that chat.");
        setActiveId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  // Offered follow-ups, refetched when the transcript changes. An empty list is
  // a normal result: the reader simply gets no buttons.
  useEffect(() => {
    if (!activeId || savedMessages.length === 0) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    fetchSuggestions(activeId)
      .then((found) => {
        if (!cancelled) setSuggestions(found);
      })
      .catch(() => {
        if (!cancelled) setSuggestions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId, savedMessages.length]);

  const startNewChat = useCallback(() => {
    setTemporary(false);
    setTemporaryMessages([]);
    setActiveId(null);
    setStreaming(null);
    setNotice(null);
    setPageError(null);
    setOpenEmailId(null);
    setMessageDetail(null);
  }, []);

  const toggleTemporary = useCallback(() => {
    setTemporary((value) => {
      const next = !value;
      // Leaving a temporary chat discards it, which is the entire point.
      if (!next) setTemporaryMessages([]);
      return next;
    });
    setStreaming(null);
    setNotice(null);
    setPageError(null);
  }, []);

  const openEmail = useCallback(
    async (emailId: string, ownerMailboxId: string | null) => {
      const target = ownerMailboxId ?? mailboxId;
      if (!target) return;
      setOpenEmailId(emailId);
      setMessageLoading(true);
      setMessageError(null);
      try {
        const found = await getMessage(target, emailId);
        setMessageDetail(found);
        const key = found.conversation_id || found.thread_key;
        if (!key) {
          setMessageThread([]);
        } else {
          // A thread that will not load must not block reading the message.
          getThread(target, key)
            .then((page) => setMessageThread(page.messages))
            .catch(() => setMessageThread([]));
        }
      } catch (cause) {
        setMessageDetail(null);
        setMessageError(cause instanceof Error ? cause.message : "Could not open that email.");
      } finally {
        setMessageLoading(false);
      }
    },
    [mailboxId],
  );

  const finishTurn = useCallback((citations: ChatCitation[], answer: string) => {
    setStreaming(null);
    setSending(false);
    const message: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "assistant",
      text: answer,
      content: {},
      citations,
      steps: [],
      created_at: new Date().toISOString(),
    };
    setTemporaryMessages((current) => [...current, message]);
  }, []);

  /**
   * Send one turn.
   *
   * The saved path creates the conversation on the first message, so a chat that
   * is never used leaves no row. The temporary path is grounded in the same
   * mailbox but writes nothing at all.
   */
  const handleSend = useCallback(
    async (text: string) => {
      setNotice(null);
      setPageError(null);
      setStreaming({ question: text, answer: "", started: false });
      setSending(true);

      const question: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        text,
        content: {},
        citations: [],
        steps: [],
        created_at: new Date().toISOString(),
      };
      if (temporary) {
        setTemporaryMessages((current) => [...current, question]);
      }

      const controller = new AbortController();
      let answer = "";
      let citations: ChatCitation[] = [];

      const onEvent = (event: ChatTurnEvent) => {
        switch (event.type) {
          case "answer_delta":
            answer += event.text;
            setStreaming((current) => (current ? { ...current, answer } : current));
            break;
          case "answer":
            // The final answer replaces the streamed text rather than adding to
            // it: the deltas and this event carry the same words.
            answer = event.text || event.explanation || answer;
            break;
          case "citations":
            citations = event.citations;
            break;
          case "done":
            // The turn is over. Without this the composer stayed disabled for
            // good after the first message, because nothing else clears it.
            setStreaming(null);
            setSending(false);
            break;
          case "error":
          case "unavailable":
            setNotice(event.message);
            setStreaming(null);
            setSending(false);
            break;
          default:
            break;
        }
      };

      try {
        if (temporary) {
          await sendTemporaryMessage(mailboxId ?? "", text, onEvent, controller.signal);
          finishTurn(citations, answer);
          if (!activeId) return;
          return;
        }

        let target = activeId;
        if (!target) {
          const chosen = mailboxes.find((mailbox) => mailbox.id === mailboxId);
          if (!chosen?.ai_consent) {
            setNotice(blockedReason ?? "Choose a connected mailbox first.");
            setStreaming(null);
            setSending(false);
            return;
          }
          const created = await createChat(chosen.id);
          setChats((current: ChatSummary[]) => [created, ...current]);
          setActiveId(created.id);
          setChatMailboxId(created.mailbox_id);
          target = created.id;
          setSavedMessages([question]);
        }

        await sendChatMessage(target, text, onEvent, controller.signal);
        const reloaded = await getChat(target);
        setSavedMessages(reloaded.messages);
        refresh();
      } catch (cause) {
        setNotice(null);
        setPageError(cause instanceof Error ? cause.message : "The assistant could not be reached.");
        setStreaming(null);
        setSending(false);
      }
    },
    [temporary, activeId, mailboxId, mailboxes, blockedReason, finishTurn, refresh, setChats],
  );

  const messages = temporary ? temporaryMessages : savedMessages;

  return (
    <main className="mail-page chat-page">
      <div className="chat-toolbar">
        <h1>Ask your inbox</h1>
        {mailboxes.length > 1 && (
          <label className="chat-mailbox">
            <span className="sr-only">Outlook account</span>
            <select
              value={mailboxId ?? ""}
              onChange={(event) => {
                setMailboxId(event.target.value);
                startNewChat();
              }}
            >
              {mailboxes.map((mailbox) => (
                <option key={mailbox.id} value={mailbox.id}>
                  {mailbox.email}
                </option>
              ))}
            </select>
          </label>
        )}
        {mailboxes.length === 1 && (
          <p className="connected-account">
            Asking about <strong>{mailboxes[0].email}</strong>
          </p>
        )}
      </div>

      <div className="mail-panes chat-panes">
        <ChatSidebar
          chats={chats}
          activeId={temporary ? null : activeId}
          temporary={temporary}
          onSelect={(id) => {
            setTemporary(false);
            setTemporaryMessages([]);
            setStreaming(null);
            setNotice(null);
            setPageError(null);
            setActiveId(id || null);
          }}
          onNewChat={startNewChat}
          onToggleTemporary={toggleTemporary}
          onChanged={setChats}
          searchToken={chats.length}
        />

        <div className="chat-main">
          {loading && <p className="status-message">Loading your chats&hellip;</p>}
          {noMailbox && (
            <p className="status-message">
              Connect Outlook to ask questions about your email.{" "}
              <Link href="/app/settings">Go to Settings</Link>
            </p>
          )}
          <ChatPanel
            messages={messages}
            streaming={streaming}
            sending={sending}
            notice={notice}
            error={pageError ?? historyError ?? mailboxError}
            suggestions={suggestions}
            onSend={(text) => void handleSend(text)}
            onOpenEmail={(emailId) => void openEmail(emailId, chatMailboxId ?? mailboxId)}
            disabled={Boolean(blockedReason)}
            disabledReason={blockedReason ?? undefined}
            temporary={temporary}
          />
        </div>

        <div className="chat-reading">
          <ReadingPane
            detail={messageDetail}
            loading={messageLoading}
            error={messageError}
            mailboxId={chatMailboxId ?? mailboxId}
            thread={messageThread}
            highlightQuote={null}
            onOpenThreadMessage={(id) => void openEmail(id, chatMailboxId ?? mailboxId)}
            onRetry={() => openEmailId && void openEmail(openEmailId, chatMailboxId ?? mailboxId)}
          />
        </div>
      </div>
    </main>
  );
}