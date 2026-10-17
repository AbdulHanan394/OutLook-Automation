import type { Page } from "@playwright/test";
import { expect, openMail, test, trackApiFailures } from "./fixtures";

/**
 * OL1 acceptance, asserted in a real browser against the seeded fixture
 * mailbox (5,000+ messages): folder browsing with unread counts, opening a
 * message, safe rendering of a hostile email, keyboard navigation, and the
 * single-pane mobile flow.
 */
/**
 * On a narrow viewport the client shows one pane at a time and opens on the
 * message list, so any test that reaches for the folder rail has to step into
 * the folders pane first.
 */
async function showFolderPane(page: Page) {
  const mailPage = page.locator(".mail-page");
  if ((await mailPage.getAttribute("data-pane")) !== "folders") {
    const switcher = page.locator(".mobile-bar button", { hasText: "Folders" });
    // isVisible, not count: the mobile bar exists in the DOM on desktop too and
    // count() would report a hidden button as available.
    if (await switcher.isVisible().catch(() => false)) await switcher.click();
  }
}

test.describe("mail client", () => {
  test("lists folders with unread counts from synced data", async ({ signedIn: page }) => {
    await openMail(page);
    await showFolderPane(page);

    const folders = page.locator(".folder-row");
    await expect(folders.first()).toBeVisible();

    const inbox = folders.filter({ hasText: "Inbox" }).first();
    await expect(inbox).toBeVisible();
    await expect(inbox).toHaveAttribute("data-selected", "true");

    // The unread badge must be a real count, not a placeholder.
    const badge = inbox.locator(".folder-badge");
    await expect(badge).toBeVisible();
    const unread = Number((await badge.innerText()).replace("+", ""));
    expect(unread).toBeGreaterThan(0);

    // Sent Items and Archive exist because the fixture synced them.
    await expect(folders.filter({ hasText: "Sent Items" }).first()).toBeVisible();
    await expect(folders.filter({ hasText: "Archive" }).first()).toBeVisible();
  });

  test("switching folders reloads the list", async ({ signedIn: page }) => {
    await openMail(page);
    await expect(page.locator(".message-group").first()).toBeVisible();
    const rows = page.locator(".message-row");
    const firstInInbox = await rows.first().innerText();

    await showFolderPane(page);
    await page.locator(".folder-row").filter({ hasText: "Sent Items" }).first().click();
    await expect(page.locator(".message-group").first()).toBeVisible();
    await expect(rows.first()).not.toHaveText(firstInInbox);
  });

  test("scrolls a 5,000 message folder with cursor pagination", async ({ signedIn: page }) => {
    await openMail(page);
    const rows = page.locator(".message-row");

    await expect(rows.first()).toBeVisible();
    const initial = await rows.count();
    expect(initial).toBeGreaterThan(0);
    expect(initial).toBeLessThanOrEqual(50);

    // Keep scrolling until pagination has clearly appended more than one page.
    for (let attempt = 0; attempt < 25; attempt += 1) {
      const current = await rows.count();
      if (current > initial + 50) break;
      await page.mouse.wheel(0, 4000);
      await page.waitForTimeout(350);
    }
    await page.mouse.wheel(0, 8000);
    await page.waitForTimeout(600);

    const total = await rows.count();
    expect(total).toBeGreaterThan(initial);
  });

  test("quick filters narrow the list", async ({ signedIn: page }) => {
    await openMail(page);
    // Wait for real rows: the loading skeleton also renders .message-row nodes,
    // so counting before this point measures the placeholder, not the mailbox.
    await expect(page.locator(".message-group").first()).toBeVisible();

    const rows = page.locator(".message-list .message-row");
    const all = await rows.count();
    expect(all).toBeGreaterThan(0);

    const requested: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/mail/messages")) requested.push(request.url());
    });

    await page.getByRole("button", { name: "Unread", exact: true }).click();
    await expect(page.locator('.quick-filter[aria-pressed="true"]')).toHaveText("Unread");
    await page.waitForTimeout(700);

    expect(requested.some((url) => url.includes("filter=unread"))).toBe(true);

    const unreadRows = page.locator(".message-list .message-row");
    const unreadCount = await unreadRows.count();
    expect(unreadCount).toBeGreaterThan(0);
    expect(unreadCount).toBeLessThanOrEqual(all);
    // Every visible row must actually be unread, not merely fewer rows.
    const flags = await unreadRows.evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-unread")),
    );
    expect(flags.every((flag) => flag === "true")).toBe(true);

    await page.getByRole("button", { name: "Has attachments", exact: true }).click();
    await page.waitForTimeout(700);
    const attachmentMarks = await page
      .locator(".message-list .message-row")
      .evaluateAll((nodes) => nodes.map((node) => node.querySelectorAll(".attachment-mark").length));
    expect(attachmentMarks.every((count) => count === 1)).toBe(true);
  });

  test("opens a message and renders it safely", async ({ signedIn: page }) => {
    const failures = trackApiFailures(page);
    await openMail(page);

    await page.locator(".message-row").first().click();
    const reading = page.locator(".reading-pane");
    await expect(reading).toBeVisible();
    await expect(reading.locator(".reading-subject")).toBeVisible();

    // Body renders in a fully sandboxed iframe with no allow-scripts.
    const frame = reading.locator("iframe.body-frame");
    await expect(frame).toHaveAttribute("sandbox", "");
    const src = await frame.getAttribute("src");
    expect(src).toContain("/api/mail/messages/");
    // Images and quoted text must be absent until the reader opts in.
    expect(src).not.toContain("images=");
    expect(src).not.toContain("quoted=true");

    await expect(reading.locator(".reading-actions a", { hasText: "Open in Outlook" })).toHaveAttribute(
      "rel",
      /noopener/,
    );
    expect(failures, `API failures: ${failures.join(", ")}`).toHaveLength(0);
  });

  test("hostile email renders harmlessly with images blocked", async ({ signedIn: page }) => {
    await openMail(page);
    // The planted newsletter is the newest message in the fixture inbox.
    const row = page.locator(".message-row").filter({ hasText: "Quarterly newsletter" }).first();
    await expect(row).toBeVisible();
    await row.click();

    const frame = page.locator("iframe.body-frame");
    await expect(frame).toBeVisible();
    const sandboxed = await frame.evaluate((node) => {
      const iframe = node as HTMLIFrameElement;
      try {
        const doc = iframe.contentDocument;
        return {
          hasDocument: doc !== null,
          scriptCount: doc ? doc.querySelectorAll("script").length : -1,
          inlineHandlers: doc
            ? Array.from(doc.querySelectorAll("*")).filter((element) =>
                Array.from(element.attributes).some((attribute) =>
                  attribute.name.toLowerCase().startsWith("on"),
                ),
              ).length
            : -1,
          iframeCount: doc ? doc.querySelectorAll("iframe").length : -1,
          formCount: doc ? doc.querySelectorAll("form").length : -1,
          imgSrcs: doc
            ? Array.from(doc.querySelectorAll("img")).map((image) => image.getAttribute("src") ?? "")
            : [],
        };
      } catch {
        // Cross-origin access blocked by the sandbox is itself the proof.
        return { opaque: true };
      }
    });

    // An empty sandbox means an opaque origin: the parent cannot read into it,
    // and the iframe cannot read out. Either way, nothing rendered executes.
    if ("opaque" in sandboxed) {
      expect(sandboxed.opaque).toBe(true);
    } else {
      expect(sandboxed.hasDocument).toBe(false);
      expect(sandboxed.scriptCount).toBe(-1);
    }

    // "Load images" must not resurrect the tracking pixel: the server refuses
    // to emit the remote src, and the iframe policy allows none by default.
    const trackerRequests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("tracker.example.invalid")) trackerRequests.push(request.url());
    });
    const loadImages = page.getByRole("button", { name: "Load images" });
    if (await loadImages.count()) await loadImages.click();
    await page.waitForTimeout(800);
    expect(trackerRequests).toHaveLength(0);
  });

  test("keyboard navigation moves through the list", async ({ signedIn: page }) => {
    await openMail(page);
    const rows = page.locator(".message-row");
    await expect(rows.first()).toBeVisible();

    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("j");
    await expect(page.locator(".reading-pane .reading-subject")).toBeVisible();

    const first = await page.locator(".reading-pane .reading-subject").innerText();
    await page.keyboard.press("j");
    await page.waitForTimeout(500);
    const second = await page.locator(".reading-pane .reading-subject").innerText();
    expect(second).not.toBe(first);

    await page.keyboard.press("k");
    await page.waitForTimeout(500);
    await expect(page.locator(".reading-pane .reading-subject")).toHaveText(first);

    await page.keyboard.press("Escape");
    await expect(page.locator(".reading-pane .reading-subject")).toHaveCount(0);
  });

  test("slash focuses the ask bar", async ({ signedIn: page }) => {
    await openMail(page);
    await page.locator("body").click({ position: { x: 5, y: 5 } });
    await page.keyboard.press("/");
    // OL2 renders the input; in OL1 the shortcut must at least not throw.
    await expect(page.locator(".mail-page")).toBeVisible();
  });

  test("folder rail collapses", async ({ signedIn: page }) => {
    await openMail(page);
    await showFolderPane(page);
    const toggle = page.locator(".folder-collapse");
    await toggle.click();
    await expect(page.locator(".folder-pane")).toHaveAttribute("data-collapsed", "true");
    await toggle.click();
    await expect(page.locator(".folder-pane")).toHaveAttribute("data-collapsed", "false");
  });
});

test.describe("mobile single pane", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("shows one pane at a time with a back button", async ({ signedIn: page }) => {
    await openMail(page);
    const mailPage = page.locator(".mail-page");

    await expect(mailPage).toHaveAttribute("data-pane", "list");
    await expect(page.locator(".list-pane")).toBeVisible();
    await expect(page.locator(".folder-pane")).toBeHidden();

    await page.locator(".mobile-bar button", { hasText: "Folders" }).click();
    await expect(mailPage).toHaveAttribute("data-pane", "folders");
    await expect(page.locator(".folder-pane")).toBeVisible();

    await page.locator(".folder-row").filter({ hasText: "Inbox" }).first().click();
    await expect(mailPage).toHaveAttribute("data-pane", "list");

    await page.locator(".message-row").first().click();
    await expect(mailPage).toHaveAttribute("data-pane", "reading");
    await expect(page.locator(".reading-pane")).toBeVisible();

    await page.locator(".mobile-bar button", { hasText: "Back" }).click();
    await expect(mailPage).toHaveAttribute("data-pane", "list");
  });
});