import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * Reproduces the two defects reported from the Ask page:
 *
 *   1. after one message the composer stayed disabled, so no second message
 *      could be sent;
 *   2. the reply appeared twice -- once in the transcript with its cited
 *      emails, then again as a bare streaming bubble.
 *
 * Both came from the same missing `done` handler, so this asserts the two
 * things a reader would notice.
 *
 *   npx tsx e2e/check-send-twice.ts
 */

const BASE = process.env.E2E_BASE_URL || "http://localhost:3000";

function session() {
  const file = path.join(process.cwd(), "..", "eval", ".demo_session.json");
  return JSON.parse(readFileSync(file, "utf-8"));
}

async function main() {
  const demo = session();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addCookies([
    { name: "email_finder_session", value: demo.session_token, url: BASE },
    { name: "csrf_token", value: demo.csrf_token, url: BASE },
  ]);
  const page = await context.newPage();
  const failures: string[] = [];

  await page.goto(`${BASE}/app/ask`, { waitUntil: "networkidle" });

  // Start from a clean chat.
  await page.getByRole("button", { name: /new chat/i }).first().click();
  await page.waitForTimeout(600);

  const box = page.locator("#chat-message");
  const send = page.locator(".chat-composer button[type=submit]");

  for (const question of [
    "summarise the most recent conversations",
    "what should I follow up on this week?",
  ]) {
    await box.fill(question);
    if (await send.isDisabled()) {
      failures.push(`composer was disabled before sending: ${question}`);
      break;
    }
    const before = await page.locator(".chat-turn-assistant").count();
    await send.click();

    // Wait for a new assistant message to appear. Waiting on "not disabled"
    // instead returns immediately, because the textarea is still enabled in
    // the frame before React applies the busy state -- which is how this test
    // passed a broken build in its first version.
    await page
      .waitForFunction(
        (previous) => document.querySelectorAll(".chat-turn-assistant").length > previous,
        before,
        { timeout: 120_000 },
      )
      .catch(() => failures.push(`no assistant message appeared for: ${question}`));

    // Then wait for the composer to come back, which is the actual regression.
    await page
      .waitForFunction(
        () => {
          const el = document.querySelector("#chat-message");
          return el instanceof HTMLTextAreaElement && !el.disabled;
        },
        undefined,
        { timeout: 60_000 },
      )
      .catch(() => failures.push("composer never re-enabled after the turn"));

    const assistantTurns = await page.locator(".chat-turn-assistant").count();
    console.log(
      `  sent ${question.slice(0, 38)} -> assistant turns on screen: ${assistantTurns}`,
    );

    // A real person does not send two turns back to back, and the provider's
    // per-minute budget does not allow it either.
    if (question.startsWith("summarise")) await page.waitForTimeout(45_000);
  }

  const total = await page.locator(".chat-turn-assistant").count();
  console.log(`  assistant messages after two turns: ${total}`);
  if (total !== 2) {
    failures.push(
      `expected exactly 2 assistant messages after 2 turns, found ${total} (a duplicate or a missing reply)`,
    );
  }

  const bodies = await page.locator(".chat-turn-assistant .chat-turn-text").allTextContents();
  const unique = new Set(bodies.map((t) => t.trim()));
  if (bodies.length !== unique.size) {
    failures.push(`duplicate assistant text on screen: ${JSON.stringify(bodies)}`);
  }

  await page.screenshot({ path: "../docs/screenshots/chat/chat-two-turns.png", fullPage: false });
  await browser.close();

  if (failures.length) {
    console.error("\nFAILED:");
    for (const failure of failures) console.error("  - " + failure);
    process.exit(1);
  }
  console.log("\nOK: composer re-enabled and each turn rendered exactly once.");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});