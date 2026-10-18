import type { Page, Route } from "@playwright/test";

/**
 * A stubbed mail API, so the layout suite needs no database.
 *
 * Everything here is about *layout*: does the shell stop scrolling, do the
 * columns scroll independently, does the rail collapse, is any label
 * unreadable. None of those questions depend on real mail, and all of them
 * depend on having a predictable amount of it -- 200+ messages for the folder
 * pane, a message long enough to scroll, an HTML email carrying its own
 * colours. Driving those through a real Postgres mailbox would make every one
 * of them a function of whatever happened to be seeded, so they are served
 * from here instead.
 *
 * The endpoints are answered with the same shapes the real API returns, so the
 * suite fails the moment the component starts expecting a different one.
 */

const MAILBOX_ID = "11111111-1111-4111-8111-111111111111";
const API = "http://localhost:8000";

export const FOLDERS = [
  { id: "inbox", name: "Inbox", total: 240, unread: 7 },
  { id: "sentitems", name: "Sent Items", total: 64, unread: 0 },
  { id: "drafts", name: "Drafts", total: 3, unread: 1 },
  { id: "archive", name: "Archive", total: 118, unread: 2 },
  { id: "deleteditems", name: "Deleted Items", total: 12, unread: 0 },
  { id: "junkemail", name: "Junk Email", total: 4, unread: 4 },
  // Configured but empty: must not appear in the folder pane at all.
  { id: "outbox", name: "Outbox", total: 0, unread: 0 },
];

const LONG_BODY = Array.from(
  { length: 120 },
  (_, index) =>
    `Paragraph ${index + 1}: the delivery date was agreed with the client for 30 April, ` +
    `conditional on the revised budget being approved by the finance team.`,
).join("\n\n");

/** An email that brings its own colours: cannot be repainted, goes on paper. */
const INLINE_COLOUR_BODY =
  '<html><body><table width="600" bgcolor="#ffffff">' +
  '<tr><td style="color:#000000;font-family:Arial">' +
  "<h2>Quarterly report</h2>" +
  '<p style="color:#000000">The figures below are in thousands.</p>' +
  "</td></tr></table></body></html>";

/**
 * A sender-designed *dark* email: a near-black table with black text on it.
 * Left alone this is unreadable, which is what the dark-background path exists
 * to fix. It is the shape real status and incident mail arrives in.
 */
const DARK_EMAIL_BODY =
  '<html><body><table width="600" bgcolor="#141419">' +
  '<tr><td style="color:#000000">' +
  "<h2>Delivery rescheduled</h2>" +
  '<p style="color:#000000">The client moved delivery to 20 November.</p>' +
  "</td></tr></table></body></html>";

export type StubOptions = {
  /** Total messages the mailbox reports. Drives the folder-pane height test. */
  total?: number;
  theme?: "light" | "dark";
};

function messageRows(count: number) {
  const now = new Date("2026-03-14T10:00:00Z").getTime();
  return Array.from({ length: count }, (_, index) => {
    const id = `20000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
    return {
      id,
      subject:
        index === 0
          ? "Revised budget approval"
          : index === 1
            ? "Quarterly report with inline colours"
            : index === 2
              ? "Delivery rescheduled (dark template)"
              : `Status update ${index}`,
      sender_name: index % 2 === 0 ? "Project Manager" : "A. Client",
      sender_address: index % 2 === 0 ? "manager@example.test" : "client@example.test",
      received_at: new Date(now - index * 3_600_000).toISOString(),
      sent_at: new Date(now - index * 3_600_000).toISOString(),
      folder: "inbox",
      preview: `Routine project note number ${index}.`,
      is_read: index % 3 !== 0,
      importance: "normal",
      is_flagged: false,
      has_attachments: index % 7 === 0,
      attachment_names: [],
      thread_count: 1,
    };
  });
}

function renderDocument(theme: "light" | "dark", path: "themed" | "paper" | "darkfix"): string {
  // Mirrors what the render endpoint emits: an opaque-origin document that
  // cannot inherit a theme and has to be told the whole palette.
  const paper = path === "paper";
  const dark = path === "darkfix";
  // Paper and dark are mirror images, and neither follows the app's theme: a
  // sender-designed light email is always shown on paper, a sender-designed
  // dark one always on a dark sheet with light ink.
  const palette = paper
    ? { surface: "#ffffff", ink: "#171516", link: "#2b3ec9" }
    : dark
      ? { surface: "#1c1a1b", ink: "#f3f0f1", link: "#a5b4fc" }
      : theme === "light"
        ? { surface: "#efeded", ink: "#171516", link: "#4338ca" }
        : { surface: "#1c1a1b", ink: "#f3f0f1", link: "#818cf8" };
  const font = "'IBM Plex Mono', ui-monospace, monospace";
  const inherit = paper
    ? ""
    : // body *, not *: a universal selector also matches <body>, which would
      // then inherit from <html> and render everything in the UA default black.
      `body *{color:inherit!important;font-family:inherit!important;${font}}`;
  const plainBody = `<pre style="white-space:pre-wrap">${LONG_BODY}</pre>`;
  // The sender's background is kept; only the ink is replaced. This is the real
  // renderer's dark path, mirrored.
  const darkBody = `<div style="background:#141419;color:#000000;padding:8px">${DARK_EMAIL_BODY}</div>`;
  const rendered = paper ? INLINE_COLOUR_BODY : dark ? darkBody : plainBody;
  const label = paper || dark ? '<p class="original-formatting">Original formatting</p>' : "";
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    `<meta name="color-scheme" content="${theme}">` +
    "<style>" +
    `body{margin:0;padding:14px;font-family:${font};font-size:13px;line-height:1.6;` +
    // One closing brace. Two here emit a stray "}" before the next rule, and
    // Chromium's error recovery drops the rule after it -- which silently
    // disables the colour override and puts the stub's own emails back to
    // black-on-black.
    `background:${palette.surface};color:${palette.ink}}` +
    inherit +
    `a{color:${palette.link}}` +
    "::selection{background:#4338ca;color:#f8f7f7}" +
    ".original-formatting{margin:0 0 10px;display:inline-block}" +
    "</style></head><body>" +
    label +
    rendered +
    "</body></html>"
  );
}

/**
 * The app talks to the API cross-origin with `credentials: "include"` and a
 * JSON content-type on every call, so every request is preceded by a preflight
 * and the response has to echo the origin rather than send `*`. Both are handled
 * here: a stubbed route that forgets them fails as a CORS error long before the
 * layout under test is ever exercised.
 */
function corsHeaders(route: Route): Record<string, string> {
  // headers() is the synchronous accessor; headerValue() returns a promise, and
  // a promise here would be serialised into the header as "[object Promise]".
  const origin = route.request().headers()["origin"] ?? "*";
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,X-CSRF-Token,X-Request-Id",
    Vary: "Origin",
  };
}

async function fulfill(route: Route, body: unknown, contentType = "application/json") {
  await route.fulfill({
    status: 200,
    contentType,
    headers: corsHeaders(route),
    body: contentType === "application/json" ? JSON.stringify(body) : String(body),
  });
}

/** Install the stubbed API. Call before `page.goto`. */
export async function stubMailApi(page: Page, options: StubOptions = {}) {
  const total = options.total ?? 240;
  const rows = messageRows(total);

  await page.route(`${API}/api/**`, async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      return route.fulfill({ status: 204, headers: corsHeaders(route), body: "" });
    }
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/mailboxes") {
      return fulfill(route, [
        {
          id: MAILBOX_ID,
          email: "reader@example.test",
          status: "active",
          ai_consent: false,
        },
      ]);
    }
    if (path === "/api/mail/folders") {
      return fulfill(route, {
        folders: FOLDERS,
        unread_total: FOLDERS.reduce((total_, folder) => total_ + folder.unread, 0),
      });
    }
    if (path === "/api/mailboxes/".concat(MAILBOX_ID, "/sync")) {
      return fulfill(route, {
        state: "complete",
        last_success_at: "2026-03-14T09:55:00Z",
      });
    }
    if (path === "/api/mail/messages" && url.searchParams.get("cursor")) {
      return fulfill(route, { messages: [], next_cursor: null, folder: "inbox", filter: "all" });
    }
    if (path === "/api/mail/messages") {
      // One page, then no more: the folder pane has to stay pinned at 240 rows
      // worth of height without any further paging.
      return fulfill(route, {
        messages: rows.slice(0, 240),
        next_cursor: null,
        folder: "inbox",
        filter: "all",
      });
    }
    const render = path.match(/^\/api\/mail\/messages\/([^/]+)\/render$/);
    if (render) {
      const theme = (url.searchParams.get("theme") ?? "light") as "light" | "dark";
      const index = Number(render[1].slice(-12));
      // Message 1 is designed for a light background; message 2 paints its own
      // near-black one. Together they cover both "cannot be recoloured" paths.
      const path = index === 1 ? "paper" : index === 2 ? "darkfix" : "themed";
      return fulfill(route, renderDocument(theme, path), "text/html");
    }
    const detail = path.match(/^\/api\/mail\/messages\/([^/]+)$/);
    if (detail) {
      const index = Number(detail[1].slice(-12));
      const row = rows[index] ?? rows[0];
      return fulfill(route, {
        ...row,
        to: [{ name: "A. Client", address: "client@example.test" }],
        cc: [],
        body_text: LONG_BODY,
        has_html: index === 1,
        has_quoted: false,
        web_link: null,
        conversation_id: null,
        thread_key: null,
      });
    }
    if (path.startsWith("/api/mail/threads/")) {
      return fulfill(route, { thread_id: url.pathname.split("/").pop(), messages: [] });
    }
    if (path === "/api/mail/ask/history") {
      return fulfill(route, { history: [], examples: [] });
    }
    return fulfill(route, {});
  });
}

export { MAILBOX_ID };