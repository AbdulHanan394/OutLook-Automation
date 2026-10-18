import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { chromium, type Page } from "@playwright/test";

/**
 * Captures the conversational Ask page at the three required widths in both
 * themes.
 *
 *   npx tsx e2e/capture-chat.ts [outDir]
 *
 * Themes are forced through the data-theme attribute so the capture does not
 * depend on the machine's prefers-color-scheme. Two states are captured per
 * width: the empty chat, and a chat with a real answer and its cited emails, so
 * the transcript, the citations and the reading pane are all visible rather than
 * assumed.
 */
const OUT = process.argv[2] || "../docs/screenshots/chat";
const BASE = process.env.E2E_BASE_URL || "http://localhost:3000";

const WIDTHS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "768", width: 768, height: 1024 },
  { name: "390", width: 390, height: 844 },
];

const THEMES = ["light", "dark"] as const;

function session() {
  const candidates = [
    path.join(process.cwd(), "..", "eval", ".demo_session.json"),
    path.join(process.cwd(), "eval", ".demo_session.json"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return JSON.parse(readFileSync(candidate, "utf-8"));
  }
  throw new Error("Run eval/seed_demo_mailbox.py first");
}

async function setTheme(page: Page, theme: string) {
  await page.evaluate((value) => {
    document.documentElement.dataset.theme = value;
    try {
      localStorage.setItem("email-finder-theme", value);
    } catch {
      /* storage unavailable: the data attribute is enough */
    }
  }, theme);
  await page.waitForTimeout(250);
}

async function main() {
  const demo = session();
  mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch();
  const taken: string[] = [];

  for (const size of WIDTHS) {
    const context = await browser.newContext({
      viewport: { width: size.width, height: size.height },
    });
    await context.addCookies([
      { name: "email_finder_session", value: demo.session_token, url: BASE },
      { name: "csrf_token", value: demo.csrf_token, url: BASE },
    ]);
    const page = await context.newPage();
    page.on("pageerror", (error) => console.error("  page error:", error.message));

    for (const theme of THEMES) {
      await page.goto(`${BASE}/app/ask`, { waitUntil: "networkidle" });
      await setTheme(page, theme);
      await page.reload({ waitUntil: "networkidle" });
      await setTheme(page, theme);
      await page.locator(".chat-sidebar").waitFor({ state: "visible", timeout: 20_000 });
      await page.waitForTimeout(600);

      const empty = path.join(OUT, `chat-${size.name}-${theme}-empty.png`);
      await page.screenshot({ path: empty });
      taken.push(empty);

      // A chat with history, so the sidebar, the transcript and the reading
      // pane are all exercised rather than assumed to render.
      const rows = page.locator(".chat-row-open");
      if (await rows.count()) {
        await rows.first().click();
        await page.waitForTimeout(900);
        const withHistory = path.join(OUT, `chat-${size.name}-${theme}-history.png`);
        await page.screenshot({ path: withHistory });
        taken.push(withHistory);
      }

      // Open a cited email in the reading pane, at the widths that have one.
      const citation = page.locator(".chat-citations button").first();
      if (await citation.count()) {
        await citation.click();
        await page.waitForTimeout(1800);
        const reading = path.join(OUT, `chat-${size.name}-${theme}-reading.png`);
        await page.screenshot({ path: reading });
        taken.push(reading);
      }
    }
    await context.close();
  }

  await browser.close();
  console.log(`Captured ${taken.length} screenshots into ${OUT}/`);
  for (const file of taken) console.log("  " + file);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});