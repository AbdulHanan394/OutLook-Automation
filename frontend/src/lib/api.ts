const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;

  constructor(status: number, message: string, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }

  /** True when the mailbox needs attention rather than the request retrying. */
  get needsSetup(): boolean {
    return this.status === 404 || this.status === 409;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }

  get isAuthFailure(): boolean {
    return this.status === 401;
  }
}

function csrf(): string {
  return (
    document.cookie.split("; ").find((x) => x.startsWith("csrf_token="))?.split("=")[1] || ""
  );
}

/**
 * Single entry point for backend calls.
 *
 * Error bodies are parsed so callers can show the server's own message: the API
 * distinguishes "no mailbox connected" (409) from "rate limited" (429) from a
 * provider outage (502), and collapsing them into one string -- as this used to
 * -- made every failure look like a network problem.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const response = await fetch(`${API}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(method !== "GET" ? { "X-CSRF-Token": csrf() } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    let message = "Request failed.";
    if (response.status === 429) message = "Too many requests. Please wait a moment.";
    else if (response.status === 401) message = "Your session has expired. Sign in again.";
    else {
      try {
        const body = await response.json();
        const detail = body?.error?.message ?? body?.detail;
        if (typeof detail === "string" && detail) message = detail;
      } catch {
        // Non-JSON error body; the status-based default stands.
      }
    }
    throw new ApiError(response.status, message, response.headers.get("X-Request-Id"));
  }

  if (response.status === 204) return undefined as T;
  return response.json();
}

export type Mailbox = {
  id: string;
  email: string;
  status: string;
  ai_consent: boolean;
};

export type Folder = {
  id: string;
  name: string;
  total: number;
  unread: number;
};

export type MessageRow = {
  id: string;
  subject: string | null;
  sender_name: string | null;
  sender_address: string | null;
  received_at: string;
  sent_at: string | null;
  folder: string;
  preview: string;
  is_read: boolean;
  importance: string;
  is_flagged: boolean;
  has_attachments: boolean;
  attachment_names: string[];
  thread_count: number;
  cursor?: string;
};

export type AddressEntry = { name: string | null; address: string | null };

export type MessageDetail = {
  id: string;
  subject: string | null;
  sender_name: string | null;
  sender_address: string | null;
  to: AddressEntry[];
  cc: AddressEntry[];
  received_at: string;
  sent_at: string | null;
  folder: string;
  body_text: string;
  has_html: boolean;
  has_quoted: boolean;
  web_link: string | null;
  is_read: boolean;
  importance: string;
  is_flagged: boolean;
  has_attachments: boolean;
  attachment_names: string[];
  conversation_id: string | null;
  thread_key: string | null;
};

export type MessageListPage = {
  messages: MessageRow[];
  next_cursor: string | null;
  folder: string;
  filter: MessageFilter;
};

export type MessageFilter = "all" | "unread" | "has_attachments";

export type ThreadPage = { thread_id: string; messages: MessageRow[] };

export function listMailboxes(): Promise<Mailbox[]> {
  return api<Mailbox[]>("/api/mailboxes");
}

export function listFolders(mailboxId: string): Promise<{ folders: Folder[]; unread_total: number }> {
  return api(`/api/mail/folders?mailbox_id=${encodeURIComponent(mailboxId)}`);
}

export function listMessages(params: {
  mailboxId: string;
  folder: string;
  filter?: MessageFilter;
  cursor?: string | null;
  limit?: number;
}): Promise<MessageListPage> {
  const query = new URLSearchParams({
    mailbox_id: params.mailboxId,
    folder: params.folder,
    filter: params.filter ?? "all",
    limit: String(params.limit ?? 50),
  });
  if (params.cursor) query.set("cursor", params.cursor);
  return api<MessageListPage>(`/api/mail/messages?${query.toString()}`);
}

export function getMessage(mailboxId: string, emailId: string): Promise<MessageDetail> {
  return api(`/api/mail/messages/${encodeURIComponent(emailId)}?mailbox_id=${encodeURIComponent(mailboxId)}`);
}

/**
 * URL for the sandboxed reading-pane iframe.
 *
 * The rendered document is served by the API with its own Content-Security-Policy
 * so the app's own policy never needs 'unsafe-inline'. `images` is only ever true
 * because the reader pressed "Load images".
 */
export function messageRenderUrl(
  mailboxId: string,
  emailId: string,
  options: { images?: boolean; quoted?: boolean; theme?: "light" | "dark" } = {},
): string {
  const query = new URLSearchParams({ mailbox_id: mailboxId });
  if (options.images) query.set("images", "true");
  if (options.quoted) query.set("quoted", "true");
  if (options.theme) query.set("theme", options.theme);
  return `${API}/api/mail/messages/${encodeURIComponent(emailId)}/render?${query.toString()}`;
}

export function getThread(mailboxId: string, threadId: string): Promise<ThreadPage> {
  return api(
    `/api/mail/threads/${encodeURIComponent(threadId)}?mailbox_id=${encodeURIComponent(mailboxId)}`,
  );
}

export type MatchReason = {
  type: "sender" | "date" | "phrase" | "subject" | "semantic" | "attachment" | "folder" | "citation";
  detail: string;
};

export type SearchCard = {
  email_id: string;
  subject: string | null;
  sender: string;
  received_at: string;
  preview: string;
  body?: string;
  outlook_url?: string | null;
  why: string[];
  reasons: MatchReason[];
  thread_count?: number;
};

/**
 * Confidence is high | low | none. "exact" is never produced by the backend and
 * the UI must not invent a fourth level -- a wrong certainty is worse than a
 * hedged one.
 */
export type Confidence = "high" | "low" | "none";

export type AskMode = "auto" | "find" | "ask";

export type AskInterpretation = {
  intent: "find" | "ask";
  keywords: string[];
  rewrites: string[];
  soft_clues: string[];
  filters: {
    sender?: string;
    after?: string;
    before?: string;
    folder?: string;
    has_attachment?: boolean;
    unread?: boolean;
  };
  /** Filters the model guessed at. Never treated as hard constraints. */
  guessed: string[];
};

export type AskEvent =
  | { type: "interpretation"; interpretation: AskInterpretationWire }
  | {
      type: "results";
      results: SearchCard[];
      confidence: Confidence;
      best_email_id?: string;
      message?: string;
    }
  | { type: "answer_delta"; text: string }
  | {
      type: "citations";
      sufficient: boolean;
      claims: AskClaim[];
      response_type?: string | null;
      timeline?: { date: string | null; email_id: string; subject: string; sender: string; text: string; quote: string }[];
    }
  | { type: "done"; used_model?: boolean; path?: string }
  | { type: "error"; message: string; fallback?: boolean; detail?: string | null };

export type AskHistoryItem = {
  id: string;
  query: string;
  mode: AskMode;
  created_at: string;
};

export type AskChip = {
  kind: string;
  label: string;
  value: string;
  guessed: boolean;
};

export type AskInterpretationWire = {
  intent: "find" | "ask";
  rewrites: string[];
  soft_clues: string[];
  chips: AskChip[];
  source: string;
  used_model: boolean;
  fallback_notice: string | null;
  fallback?: boolean;
  filters: {
    sender: string | null;
    after: string | null;
    before: string | null;
    folder: string | null;
    has_attachment: boolean;
    unread: boolean | null;
  };
  unknown_operators: string[];
};

export type AskClaim = {
  text: string;
  type: "explicit" | "inferred";
  email_ids: string[];
  quote: string;
};

/**
 * Streams the Ask endpoint.
 *
 * EventSource cannot POST, so this reads the body incrementally and dispatches
 * typed events as they arrive. Callers get results the moment retrieval finishes
 * rather than when the answer text completes.
 */
export async function askStream(
  params: { mailboxId: string; query: string; mode: AskMode; sessionId?: string },
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API}/api/mail/ask`, {
    method: "POST",
    credentials: "include",
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf(),
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      mailbox_id: params.mailboxId,
      query: params.query,
      mode: params.mode,
      session_id: params.sessionId ?? null,
    }),
  });

  if (!response.ok) {
    let message = "The AI assistant is unavailable right now.";
    if (response.status === 429) message = "Too many requests. Please wait a moment.";
    else if (response.status === 409) message = "Turn on AI processing in Settings first.";
    else {
      try {
        const body = await response.json();
        const detail = body?.error?.message ?? body?.detail;
        if (typeof detail === "string" && detail) message = detail;
      } catch {
        /* keep the default */
      }
    }
    throw new ApiError(response.status, message, response.headers.get("X-Request-Id"));
  }

  const reader = response.body?.getReader();
  if (!reader) throw new ApiError(500, "The assistant returned no stream.", null);

  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (block: string) => {
    let name = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!name || !data) return;
    try {
      const payload = JSON.parse(data) as Record<string, unknown>;
      // The server names the frame in the SSE `event:` field; the data payload
      // itself has no discriminator, so it is injected here.
      onEvent({ ...payload, type: name } as unknown as AskEvent);
    } catch {
      /* a malformed frame must not kill the stream */
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split: number;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        dispatch(buffer.slice(0, split));
        buffer = buffer.slice(split + 2);
      }
    }
    if (buffer.trim()) dispatch(buffer);
  } finally {
    reader.releaseLock();
  }
}

export async function askHistory(mailboxId: string): Promise<{ history: AskHistoryItem[]; examples: string[] }> {
  return api(`/api/mail/ask/history?mailbox_id=${encodeURIComponent(mailboxId)}`);
}

export async function recordAsk(mailboxId: string, query: string, mode: AskMode): Promise<void> {
  await api("/api/mail/ask/history", {
    method: "POST",
    body: JSON.stringify({ mailbox_id: mailboxId, query, mode }),
  });
}

export async function clearAskHistory(): Promise<void> {
  await api("/api/mail/ask/history", { method: "DELETE" });
}