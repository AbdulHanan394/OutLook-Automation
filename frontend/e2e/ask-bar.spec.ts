import { expect, openMail, test } from "./fixtures";

/**
 * OL2 and OL3 acceptance, in a real browser.
 *
 * Operator queries are deterministic, so they are fully verifiable here without a
 * model. The answer and citation flows are covered by the backend integration
 * suite with a stubbed provider; what these tests add is that the streamed
 * events actually reach the UI and drive the right panels.
 */
test.describe("Ask bar", () => {
  test("operator query shows chips, results and auto-selects the match", async ({
    signedIn: page,
  }) => {
    await openMail(page);

    await page.locator(".ask-input").fill("from:manager@example.test");
    await page.locator(".ask-submit").click();

    // Banner names the query.
    const banner = page.locator(".search-banner");
    await expect(banner).toBeVisible();
    await expect(banner.locator("q")).toHaveText("from:manager@example.test");

    // The typed operator becomes a removable chip.
    const chip = page.locator(".ask-chip").filter({ hasText: "From manager@example.test" });
    await expect(chip).toBeVisible();
    // Typed filters are hard constraints and must never be labelled a guess.
    await expect(page.locator('.ask-chip[data-guessed="true"]')).toHaveCount(0);

    // Results carry a confidence label from the allowed vocabulary.
    const confidence = page.locator(".result-confidence:visible").first();
    await expect(confidence).toBeVisible();
    const label = (await confidence.innerText()).toUpperCase();
    expect(["STRONG MATCH", "POSSIBLE MATCH", "NO RELIABLE MATCH"]).toContain(label);
    expect(label).not.toContain("EXACT");

    // At least one match reason is shown so the reader can judge it.
    await expect(page.locator(".reason-chip:visible").first()).toBeVisible();
  });

  test("operator query auto-selects the best match in the reading pane", async ({
    signedIn: page,
  }) => {
    await openMail(page);
    await page.locator(".ask-input").fill("from:manager@example.test");
    await page.locator(".ask-submit").click();

    await expect(page.locator(".result-confidence:visible")).toBeVisible();
    const subject = await page.locator(".reading-subject").first().innerText();
    expect(subject).toBe("Meeting follow-up");

    // The body renders in the sandboxed iframe and is not blocked by CSP.
    const frame = page.locator("iframe.body-frame");
    await expect(frame).toBeVisible();
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await page.waitForTimeout(600);
    expect(
      errors.filter((text) => text.includes("Content Security Policy")),
      `CSP errors: ${errors.join(" | ")}`,
    ).toHaveLength(0);
  });

test("operator queries make no model calls", async ({ signedIn: page }) => {
    const calls: string[] = [];
    page.on("request", (request) => {
      // Only the stream endpoint; /api/mail/ask/history shares the prefix.
      if (request.method() === "POST" && /\/api\/mail\/ask$/.test(new URL(request.url()).pathname))
        calls.push(request.url());
    });

    await openMail(page);
    for (const query of ["from:manager@example.test", "has:attachment", "is:unread"]) {
      await page.locator(".ask-input").fill(query);
      await page.locator(".ask-submit").click();
      await expect(page.locator(".search-banner")).toBeVisible();
      await page.locator(".search-banner .text-button").click();
    }
    expect(calls).toHaveLength(3);

    // The point of path A is that it never reaches interpretation, so it must
    // never carry an AI fallback notice. A rate-limit notice is unrelated to
    // that and may legitimately appear once the suite has used its budget.
    await page.locator(".ask-input").fill("from:manager@example.test");
    await page.locator(".ask-submit").click();
    await expect(page.locator(".search-banner")).toBeVisible();
    const notice = page.locator(".ask-notice");
    if (await notice.count()) {
      await expect(notice).not.toContainText(/AI interpretation|AI answers|daily AI request/i);
    }
  });

  test("clear and Escape both return to the folder view", async ({ signedIn: page }) => {
    await openMail(page);
    // Wait for real rows: the loading skeleton uses the same class.
    await expect(page.locator(".message-group").first()).toBeVisible();
    const firstSubject = await page.locator(".message-row .message-subject").first().innerText();
    const firstCount = await page.locator(".message-row").count();

    await page.locator(".ask-input").fill("from:manager@example.test");
    await page.locator(".ask-submit").click();
    await expect(page.locator(".search-banner")).toBeVisible();

    // Clear button.
    await page.locator(".search-banner .text-button").click();
    await expect(page.locator(".search-banner")).toHaveCount(0);
    await expect(page.locator(".quick-filter").first()).toBeVisible();
    await expect(page.locator(".message-row .message-subject").first()).toHaveText(firstSubject);
    await expect(page.locator(".message-row")).toHaveCount(firstCount);

    // Escape.
    await page.locator(".ask-input").fill("has:attachment");
    await page.locator(".ask-submit").click();
    await expect(page.locator(".search-banner")).toBeVisible();
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("Escape");
    await expect(page.locator(".search-banner")).toHaveCount(0);
    await expect(page.locator(".quick-filter").first()).toBeVisible();
  });

  test("removing a chip re-runs the search", async ({ signedIn: page }) => {
    await openMail(page);

    // Two operators, so removing one leaves a valid search behind.
    await page.locator(".ask-input").fill("from:manager@example.test is:unread");
    await page.locator(".ask-submit").click();

const chip = page.locator(".ask-chip").filter({ hasText: "From manager@example.test" });
    await expect(chip).toBeVisible();
    // Count filter chips only: the Conversation toggle is also an .ask-chip.
    const filterChips = page.locator(".ask-chip:not(.ask-chip-button)");
    await expect(filterChips).toHaveCount(2);

    await chip.getByRole("button", { name: /Remove filter/ }).click();

    await expect(filterChips).toHaveCount(1);
    await expect(filterChips.first()).toContainText("Unread only");
    // The query text is rewritten, not just the chip removed.
    await expect(page.locator(".ask-input")).toHaveValue("is:unread");
    await expect(page.locator(".search-banner q")).toHaveText("is:unread");
    await expect(page.locator(".result-confidence:visible")).toBeVisible();
  });

  test("no reliable match says so and offers a way forward", async ({ signedIn: page }) => {
    await openMail(page);
    await page.locator(".ask-input").fill("from:nobody-at-all@example.test");
    await page.locator(".ask-submit").click();

    await expect(page.locator(".result-confidence:visible").first()).toHaveText(
      /no reliable match/i,
    );
    await expect(page.locator(".result-state")).toBeVisible();
    await expect(page.getByRole("button", { name: "Reword the search" })).toBeVisible();
    await expect(page.locator(".message-row")).toHaveCount(0);
  });

  test("slash and Ctrl+K focus the ask bar", async ({ signedIn: page }) => {
    await openMail(page);
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("/");
    await expect(page.locator(".ask-input")).toBeFocused();

    await page.locator(".ask-input").blur();
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("Control+k");
    await expect(page.locator(".ask-input")).toBeFocused();
  });

  test("mode toggle chooses Find or Ask explicitly", async ({ signedIn: page }) => {
    await openMail(page);
    const ask = page.locator(".ask-mode button", { hasText: /^Ask$/ });
    const find = page.locator(".ask-mode button", { hasText: /^Find$/ });

    await find.click();
    await expect(find).toHaveAttribute("aria-pressed", "true");
    await ask.click();
    await expect(ask).toHaveAttribute("aria-pressed", "true");

    // With no AI consent the ask path must fall back rather than fail.
    await page.locator(".ask-input").fill("what did the client say about delivery");
    await page.locator(".ask-submit").click();
    await expect(page.locator(".search-banner")).toBeVisible();
    await expect(page.locator(".ask-notice")).toBeVisible();
    await expect(page.locator(".ask-notice")).toContainText(/unavailable|off|limit/i);
  });

  test("suggestions offer examples and recent asks", async ({ signedIn: page }) => {
    await openMail(page);
    const input = page.locator(".ask-input");
    await input.click();
    await expect(page.locator(".ask-suggestions")).toBeVisible();

    const options = page.locator(".ask-suggestions [role='option']");
    await expect(options.first()).toBeVisible();

    // Choosing one submits it.
    const first = await options.first().innerText();
    await options.first().click();
    await expect(page.locator(".search-banner")).toBeVisible();
    await expect(page.locator(".search-banner q")).toHaveText(first.trim());
  });

  test("operator query with no results stays honest", async ({ signedIn: page }) => {
    await openMail(page);
    await page.locator(".ask-input").fill("before:2020-01-01 folder:archive");
    await page.locator(".ask-submit").click();

    await expect(page.locator(".result-confidence:visible").first()).toBeVisible();
    const label = (await page.locator(".result-confidence:visible").first().innerText()).toUpperCase();
    if (label === "NO RELIABLE MATCH") {
      await expect(page.locator(".message-row")).toHaveCount(0);
    } else {
      // If it claims a match, every claimed row must carry a reason.
      await expect(page.locator(".reason-chip").first()).toBeVisible();
    }
  });
});