import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { chromium, type Page } from "@playwright/test";

/**
 * Captures the mail client at the three required widths in both themes.
 *
 *   npx tsx e2e/capture.ts [outDir]
 *
 * Themes are forced by setting data-theme on <html> before the screenshot so the
 * capture does not depend on the machine's prefers-color-scheme.
 */
const OUT = process.argv[2] || "screenshots";
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
      { name: "csrf_token", value: "shot", url: BASE },
    ]);
    const page = await context.newPage();

    for (const theme of THEMES) {
      await page.goto(`${BASE}/app/mail`, { waitUntil: "networkidle" });
      await page.evaluate((t) => {
        document.documentElement.dataset.theme = t;
        localStorage.setItem("email-finder-theme", t);
      }, theme);
      await page.reload({ waitUntil: "networkidle" });
      await setTheme(page, theme);
      // Let the message list settle before capturing.
      await page
        .locator(".message-row")
        .first()
        .waitFor({ state: "visible", timeout: 20_000 })
        .catch(() => undefined);
      await page.waitForTimeout(700);

      const listShot = path.join(OUT, `mail-${size.name}-${theme}.png`);
      await page.screenshot({ path: listShot });
      taken.push(listShot);

      // And the reading pane, which is where the safe rendering lives.
      const row = page.locator(".message-row").first();
      if (await row.count()) {
        await row.click();
        await page.locator(".reading-pane").waitFor({ state: "visible", timeout: 20_000 });
        await page.waitForTimeout(900);
        const readShot = path.join(OUT, `reading-${size.name}-${theme}.png`);
        await page.screenshot({ path: readShot });
        taken.push(readShot);
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