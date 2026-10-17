import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, test as base, type Page } from "@playwright/test";

/**
 * Session fixture for the E2E suite.
 *
 * The suite signs in with a pre-seeded session token rather than driving the
 * OAuth flow, because the flow needs a live Microsoft tenant. Seed one with:
 *
 *   python eval/seed_demo_mailbox.py --size 5000 --out eval/.demo_session.json
 */
export type DemoSession = {
  session_token: string;
  user_id: string;
  mailbox_id: string;
  csrf_token: string;
  account_email: string;
  messages_indexed: number;
  corpus_size: number;
};

const SESSION_PATHS = [
  path.join(process.cwd(), "..", "eval", ".demo_session.json"),
  path.join(process.cwd(), "eval", ".demo_session.json"),
  process.env.E2E_SESSION_FILE || "",
].filter(Boolean);

export function loadSession(): DemoSession {
  for (const candidate of SESSION_PATHS) {
    if (candidate && existsSync(candidate)) {
      return JSON.parse(readFileSync(candidate, "utf-8")) as DemoSession;
    }
  }
  throw new Error(
    "No demo session found. Run: python eval/seed_demo_mailbox.py --size 5000 --out eval/.demo_session.json",
  );
}

type MailFixtures = {
  session: DemoSession;
  /** A page that already carries a valid session cookie. */
  signedIn: Page;
};

export const test = base.extend<MailFixtures>({
  session: async ({}, use) => {
    await use(loadSession());
  },

  signedIn: async ({ page, context, session, baseURL }, use) => {
    // URL-scoped rather than an explicit domain: Playwright refuses to set
    // domain="localhost" cookies on some versions, and the mail client needs the
    // session before its first /api/mail call.
    const origin = baseURL ?? "http://localhost:3000";
    await context.addCookies([
      { name: "email_finder_session", value: session.session_token, url: origin },
      { name: "csrf_token", value: session.csrf_token, url: origin },
    ]);
    await use(page);
  },
});

export { expect };

export async function openMail(page: Page, target = "/app/mail") {
  await page.goto(target);
  await expect(page.locator(".mail-page")).toBeVisible();
}

/** Records any 4xx/5xx from the API so a test can assert the UI made none. */
export function trackApiFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/") && response.status() >= 400) {
      failures.push(`${response.status()} ${response.url()}`);
    }
  });
  return failures;
}