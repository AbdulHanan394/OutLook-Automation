import { ApiError, api } from "./api";

/**
 * Client for the conversational Ask endpoints.
 *
 * Mirrors `/api/chats` exactly. A conversation belongs to one mailbox: it is
 * created against a mailbox and answers are grounded in that mailbox's mail
 * only, so `mailbox_id` is fixed for the life of a chat rather than chosen per
 * turn.
 */

export type ChatSummary = {
  id: string;
  mailbox_id: string | null;
  title: string;
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
  message_count?: number;
};

export type ChatCitation = {
  email_id: string;
  subject: string;
  sender: string;
  received_at: string;
  outlook_url?: string | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  content: { sufficient_evidence?: boolean; explanation?: string };
  citations: ChatCitation[];
  steps: unknown[];
  created_at: string;
};

export type ChatDetail = ChatSummary & {
  messages: ChatMessage[];
  working_set: string[];
};

export function listChats(): Promise<ChatSummary[]> {
  return api<{ conversations: ChatSummary[] }>("/api/chats").then((data) => data.conversations);
}

export function createChat(mailboxId: string | null, title?: string): Promise<ChatSummary> {
  return api<ChatSummary>("/api/chats", {
    method: "POST",
    body: JSON.stringify({ mailbox_id: mailboxId, title }),
  });
}

export function getChat(id: string): Promise<ChatDetail> {
  return api<ChatDetail>(`/api/chats/${id}`);
}

export function updateChat(
  id: string,
  patch: { title?: string; pinned?: boolean; archived?: boolean },
): Promise<ChatSummary> {
  return api<ChatSummary>(`/api/chats/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export function deleteChat(id: string): Promise<void> {
  return api<void>(`/api/chats/${id}`, { method: "DELETE" });
}

export function searchChats(query: string): Promise<ChatSummary[]> {
  return api<{ conversations: ChatSummary[] }>(`/api/chats/search?q=${encodeURIComponent(query)}`).then(
    (data) => data.conversations,
  );
}

export type ChatTurnEvent =
  | { type: "message_saved"; messageId: string }
  | { type: "answer_delta"; text: string }
  | { type: "answer"; text: string; explanation?: string; sufficient_evidence?: boolean }
  | { type: "citations"; citations: ChatCitation[] }
  | { type: "done"; usedModel: boolean; path: string }
  | { type: "error"; message: string }
  | { type: "unavailable"; message: string };

/**
 * Send one turn and read the reply as it arrives.
 *
 * The answer is verified server-side before the first delta leaves, so a
 * partial stream is never shown as if it were unverified text. `onDelta` is
 * therefore presentational only.
 */
// --------------------------------------------------------------- suggestions

/**
 * Follow-up questions this reader might ask next.
 *
 * Offered, never applied: the caller renders them as buttons and nothing is
 * written to the composer unless the reader presses one. An empty list is a
 * normal answer, not a failure.
 */
export function fetchSuggestions(id: string): Promise<string[]> {
  return api<{ suggestions: string[] }>(`/api/chats/${id}/suggestions`).then(
    (data) => data.suggestions,
  );
}

// -------------------------------------------------------------------- memory

export type MemoryNoteItem = {
  id: string;
  note_text: string;
  mailbox_id: string | null;
  use_count: number;
  created_at: string;
};

/**
 * The preferences the assistant holds, so they can be audited and removed.
 *
 * Nothing is inferred and nothing is stored that the reader did not ask for, so
 * this list is short and every line is a sentence they wrote.
 */
export function listMemoryNotes(): Promise<MemoryNoteItem[]> {
  return api<{ notes: MemoryNoteItem[] }>("/api/chats/memory").then((data) => data.notes);
}

export function deleteMemoryNote(id: string): Promise<void> {
  return api<void>(`/api/chats/memory/${id}`, { method: "DELETE" });
}

export function deleteAllMemoryNotes(): Promise<number> {
  return api<{ deleted: number }>("/api/chats/memory/delete-all", { method: "POST" }).then(
    (data) => data.deleted,
  );
}

export async function sendChatMessage(
  id: string,
  message: string,
  onEvent: (event: ChatTurnEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamTurn(`/api/chats/${id}/messages`, { message }, onEvent, signal);
}

/**
 * Ask a question that leaves nothing behind.
 *
 * Grounded in the same mail and verified the same way, but the server creates
 * no conversation, message or context row: the transcript lives only in this
 * tab and is discarded when the chat is closed.
 */
export async function sendTemporaryMessage(
  mailboxId: string,
  message: string,
  onEvent: (event: ChatTurnEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamTurn("/api/chats/temporary/messages", { mailbox_id: mailboxId, message }, onEvent, signal);
}

async function streamTurn(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: ChatTurnEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
  const csrf =
    document.cookie.split("; ").find((x) => x.startsWith("csrf_token="))?.split("=")[1] || "";

  const response = await fetch(`${base}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    let detail = `The assistant could not be reached (${response.status}).`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* the body was not JSON; keep the status-based message */
    }
    throw new ApiError(response.status, detail, response.headers.get("X-Request-Id"));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "";
  let data = "";

  const flush = () => {
    if (!eventName) return;
    let parsed: Record<string, unknown> = {};
    try {
      parsed = JSON.parse(data || "{}");
    } catch {
      parsed = {};
    }
    const payload = parsed as {
      message_id?: string;
      text?: string;
      explanation?: string;
      sufficient_evidence?: boolean;
      citations?: ChatCitation[];
      used_model?: boolean;
      path?: string;
      message?: string;
    };
    switch (eventName) {
      case "message_saved":
        onEvent({ type: "message_saved", messageId: payload.message_id ?? "" });
        break;
      case "answer_delta":
        onEvent({ type: "answer_delta", text: payload.text ?? "" });
        break;
      case "answer":
        onEvent({
          type: "answer",
          text: payload.text ?? "",
          explanation: payload.explanation,
          sufficient_evidence: payload.sufficient_evidence,
        });
        break;
      case "citations":
        onEvent({ type: "citations", citations: payload.citations ?? [] });
        break;
      case "done":
        onEvent({ type: "done", usedModel: Boolean(payload.used_model), path: payload.path ?? "" });
        break;
      case "error":
        onEvent({ type: "error", message: payload.message ?? "Something went wrong." });
        break;
      case "unavailable":
        onEvent({ type: "unavailable", message: payload.message ?? "The assistant is unavailable." });
        break;
      default:
        break;
    }
    eventName = "";
    data = "";
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split: number;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) eventName = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      flush();
    }
  }
  flush();
}