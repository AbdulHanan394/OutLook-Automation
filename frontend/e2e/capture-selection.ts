import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * Captures text selection in both themes.
 *
 * Two places can be selected and they are different documents: the app shell,
 * and the sandboxed email iframe, which inherits nothing from its embedder and
 * carries its own stylesheet. A selection that reads correctly in one and not
 * the other is exactly the bug this was written to catch.
 *
 *   npx tsx e2e/capture-selection.ts
 */

const BASE = process.env.E2E_BASE_URL || "http://localhost:3000";

function session() {
  return JSON.parse(
    readFileSync(path.join(process.cwd(), "..", "eval", ".demo_session.json"), "utf-8"),
  );
}

async function main() {
  const demo = session();
  const browser = await chromium.launch();
  const out = "../docs/screenshots/selection";

  for (const theme of ["light", "dark"] as const) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 820 } });
    await context.addCookies([
      { name: "email_finder_session", value: demo.session_token, url: BASE },
      { name: "csrf_token", value: demo.csrf_token, url: BASE },
    ]);
    const page = await context.newPage();
    await page.goto(`${BASE}/app/mail`, { waitUntil: "networkidle" });
    await page.evaluate((t) => {
      document.documentElement.dataset.theme = t;
      localStorage.setItem("email-finder-theme", t);
    }, theme);
    await page.reload({ waitUntil: "networkidle" });

    // App shell: select the first row's preview text.
    const row = page.locator(".message-row").first();
    await row.waitFor({ state: "visible", timeout: 20_000 }).catch(() => undefined);
    await page.waitForTimeout(600);
    await row.click().catch(() => undefined);
    await page.waitForTimeout(800);
    await page.evaluate(() => {
      const target = document.querySelector(".reading-pane") ?? document.body;
      const range = document.createRange();
      range.selectNodeContents(target);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${out}/shell-${theme}.png` });

    // Email frame: select inside the iframe, which has its own document.
    const frame = page.frameLocator("iframe");
    const body = frame.locator("body");
    if (await body.count()) {
      await page.evaluate(() => {
        const el = document.querySelector("iframe");
        if (!(el instanceof HTMLIFrameElement) || !el.contentDocument) return;
        const doc = el.contentDocument;
        const range = doc.createRange();
        range.selectNodeContents(doc.body);
        const selection = doc.defaultView?.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
      });
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${out}/email-${theme}.png` });
    }
    await context.close();
  }

  await browser.close();
  console.log("selection screenshots written to", out);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});