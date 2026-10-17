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
  options: { images?: boolean; quoted?: boolean } = {},
): string {
  const query = new URLSearchParams({ mailbox_id: mailboxId });
  if (options.images) query.set("images", "true");
  if (options.quoted) query.set("quoted", "true");
  return `${API}/api/mail/messages/${encodeURIComponent(emailId)}/render?${query.toString()}`;
}

export function getThread(mailboxId: string, threadId: string): Promise<ThreadPage> {
  return api(
    `/api/mail/threads/${encodeURIComponent(threadId)}?mailbox_id=${encodeURIComponent(mailboxId)}`,
  );
}

export type MatchReason = {
  type: "sender" | "date" | "phrase" | "subject" | "semantic" | "attachment";
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
  | { type: "interpretation"; interpretation: AskInterpretation }
  | { type: "results"; results: SearchCard[]; confidence: Confidence; message?: string }
  | { type: "answer_delta"; text: string }
  | {
      type: "citations";
      claims: { text: string; type: "explicit" | "inferred"; email_ids: string[]; quote: string }[];
    }
  | { type: "done" }
  | { type: "error"; message: string; fallback?: boolean };

export type AskHistoryItem = { id: string; query: string; mode: AskMode; created_at: string };