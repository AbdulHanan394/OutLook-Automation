import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { FOLDERS, stubMailApi } from "./stub-api";

/**
 * Review artifacts, written where this repo already keeps its screenshots
 * (docs/screenshots/ol1 and siblings) rather than into a gitignored directory
 * that would be thrown away with the last run.
 */
const SHOTS = path.join(process.cwd(), "..", "docs", "screenshots", "mail-shell");
mkdirSync(SHOTS, { recursive: true });

/**
 * The mail screen as an application shell.
 *
 * Every test here is about the *screen*, not about mail: that the document does
 * not scroll, that the three columns scroll on their own, that the folder rail
 * collapses and remembers itself, and that nothing on the screen is
 * unreadable in either theme. The mail behind it is stubbed, which is what
 * makes a 240-message mailbox and a body long enough to scroll reproducible.
 */

const VIEWPORTS = [
  { name: "1440x800", width: 1440, height: 800 },
  { name: "1024x700", width: 1024, height: 700 },
] as const;

const THEMES = ["light", "dark"] as const;

type Options = {
  theme: "light" | "dark";
  width: number;
  height: number;
  /** Seed the folder-pane preference before the app boots. */
  foldersCollapsed?: boolean;
  total?: number;
};

async function openMail(page: Page, options: Options) {
  await stubMailApi(page, { total: options.total ?? 240 });
  // addInitScript rather than page.evaluate: these have to be written before
  // the app reads them, and localStorage is not reachable from about:blank.
  // The key is repeated inside the browser function because that closure is
  // serialised, so it cannot capture anything from this module.
  await page.addInitScript(
    ({ theme, collapsed }) => {
      localStorage.setItem("email-finder-theme", theme);
      if (collapsed !== undefined) {
        localStorage.setItem("email-finder-folders-collapsed", String(collapsed));
      }
    },
    { theme: options.theme, collapsed: options.foldersCollapsed },
  );
  await page.setViewportSize({ width: options.width, height: options.height });
  await page.goto("/app/mail");
  await expect(page.locator(".mail-page")).toBeVisible();
  await expect(page.locator(".message-row").first()).toBeVisible();
  // Open a message so the reading pane has its own scrollable content.
  await page.locator(".message-row").first().click();
  await expect(page.locator(".reading-subject")).toBeVisible();
  await page.waitForTimeout(250);
}

/** The rendered document, reached the way a reader reaches it. */
function emailFrame(page: Page) {
  return page.frameLocator("iframe.body-frame");
}

/**
 * Scroll inside the email document. The frame is sandboxed with an empty
 * attribute, so it is an opaque origin: the parent cannot read contentDocument
 * and has to go through the frame locator like any other frame.
 */
async function scrollEmail(page: Page, amount: number) {
  return emailFrame(page)
    .locator("body")
    .evaluate((node, delta) => {
      const doc = node.ownerDocument;
      const scroller = doc.scrollingElement ?? doc.documentElement;
      const before = scroller.scrollTop;
      scroller.scrollTop = before + delta;
      return { before, after: scroller.scrollTop };
    }, amount);
}

async function emailBodyColours(page: Page) {
  return emailFrame(page)
    .locator("body")
    .evaluate((node) => {
      const parse = (value: string) => {
        const m = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [0, 0, 0];
      };
      const lum = (c: number[]) => {
        const f = (x: number) => {
          const v = x / 255;
          return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
      };
      const style = getComputedStyle(node);
      const [hi, lo] = [lum(parse(style.color)), lum(parse(style.backgroundColor))].sort(
        (a, b) => b - a,
      );
      return {
        ratio: Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100,
        color: style.color,
        background: style.backgroundColor,
      };
    });
}

function scrollTop(page: Page, selector: string) {
  return page.locator(selector).evaluate((node) => node.scrollTop);
}

/** How far a scroll container can actually go, so no test needs a magic number. */
async function maxScroll(page: Page, selector: string) {
  return page
    .locator(selector)
    .evaluate((node) => node.scrollHeight - node.clientHeight);
}

/**
 * The shell only exists above 768px, and these cases pin their own viewport,
 * so running them again under the mobile and tablet projects would only repeat
 * the desktop assertions at the desktop size. Skipped from inside the body
 * rather than with a describe-level condition, whose typing in this version of
 * Playwright rejects the two-argument callback.
 */
function shellTest(title: string, body: ({ page }: { page: Page }) => Promise<void>) {
  test(title, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "the mail shell is a desktop layout");
    await body({ page });
  });
}

/* -------------------------------------------------------------------------- */
/* contrast                                                                   */
/* -------------------------------------------------------------------------- */

/**
 * Every visible run of text on the screen, with its effective colour pair.
 *
 * Resolves the background by walking ancestors until one actually paints, and
 * flattens alpha, because a translucent token over a translucent token is the
 * normal case in this app and comparing the un-composited values would pass
 * almost anything.
 */
async function textContrast(page: Page): Promise<
  { text: string; ratio: number; colour: string; background: string }[]
> {
  return page.evaluate(() => {
    const parse = (value: string) => {
      const match = value.match(/rgba?\(([^)]+)\)/);
      if (!match) return null;
      const parts = match[1].split(/[\s,/]+/).filter(Boolean).map(Number);
      return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    };
    const over = (top: { r: number; g: number; b: number; a: number }, bottom: string) => {
      const base = parse(bottom) ?? { r: 255, g: 255, b: 255, a: 1 };
      const mix = (x: number, y: number) => Math.round(x * top.a + y * (1 - top.a));
      return `rgb(${mix(top.r, base.r)}, ${mix(top.g, base.g)}, ${mix(top.b, base.b)})`;
    };
    const luminance = (rgb: string) => {
      const { r, g, b } = parse(rgb)!;
      const channel = (c: number) => {
        const v = c / 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    };
    const ratio = (a: string, b: string) => {
      const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (high + 0.05) / (low + 0.05);
    };
    const effectiveBackground = (node: Element): string => {
      let element: Element | null = node;
      const stack: { r: number; g: number; b: number; a: number }[] = [];
      while (element) {
        const style = getComputedStyle(element);
        const colour = parse(style.backgroundColor);
        if (colour && colour.a > 0) {
          stack.push(colour);
          if (colour.a === 1) break;
        }
        element = element.parentElement;
      }
      let result = "rgb(255, 255, 255)";
      for (let i = stack.length - 1; i >= 0; i -= 1) result = over(stack[i], result);
      return result;
    };

    const results: {
      text: string;
      ratio: number;
      colour: string;
      background: string;
    }[] = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const parent = node.parentElement;
      const text = (node.textContent ?? "").trim();
      if (parent && text) {
        const style = getComputedStyle(parent);
        const box = parent.getBoundingClientRect();
        const visible =
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          Number(style.opacity) > 0.05 &&
          box.width > 0 &&
          box.height > 0;
        if (visible) {
          // Text in a collapsed tooltip is intentionally hidden until hover.
          const hiddenByDesign = parent.classList.contains("folder-tooltip");
          if (!hiddenByDesign) {
            const raw = parse(style.color)!;
            const background = effectiveBackground(parent);
            const colour = raw.a === 1 ? style.color : over(raw, background);
            results.push({
              text: text.slice(0, 60),
              ratio: Math.round(ratio(colour, background) * 100) / 100,
              colour,
              background,
            });
          }
        }
      }
      node = walker.nextNode();
    }
    return results;
  });
}

async function expectReadable(page: Page, label: string) {
  const entries = (await textContrast(page)).filter((entry) => entry.text.length > 1);
  expect(entries.length, `no text found to check in ${label}`).toBeGreaterThan(5);
  const failures = entries
    .filter((entry) => entry.ratio < 4.5)
    .map((entry) => `${entry.text} (${entry.ratio}:1, ${entry.colour} on ${entry.background})`);
  expect(failures, `contrast below 4.5:1 in ${label}:\n${failures.join("\n")}`).toEqual([]);
}

/* -------------------------------------------------------------------------- */

for (const viewport of VIEWPORTS) {
  for (const theme of THEMES) {
    test.describe(`${viewport.name} / ${theme}`, () => {
      test.use({ viewport: { width: viewport.width, height: viewport.height } });

      const options: Options = { theme, width: viewport.width, height: viewport.height };
      const shot = (name: string) => path.join(SHOTS, `mail-${viewport.name}-${theme}-${name}.png`);

      shellTest("the document itself does not scroll", async ({ page }) => {
        await openMail(page, options);

        const metrics = await page.evaluate(() => ({
          scrollHeight: document.scrollingElement!.scrollHeight,
          innerHeight: window.innerHeight,
          bodyOverflow: getComputedStyle(document.body).overflow,
        }));

        expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.innerHeight);
        expect(metrics.bodyOverflow).toBe("hidden");
      });

      shellTest("scrolling the list leaves the reading pane where it was", async ({ page }) => {
        await openMail(page, options);

        await page.locator(".reading-pane").evaluate((node) => {
          node.scrollTop = 120;
        });
        const before = await scrollTop(page, ".reading-pane");

        await page.locator(".list-scroll").evaluate((node) => {
          node.scrollTop += 500;
        });
        await page.waitForTimeout(150);

        expect(await scrollTop(page, ".list-scroll")).toBeGreaterThan(400);
        expect(await scrollTop(page, ".reading-pane")).toBe(before);
      });

      shellTest("a long email scrolls only inside the reading pane", async ({ page }) => {
        await openMail(page, options);
        const listBefore = await scrollTop(page, ".list-scroll");
        const room = await maxScroll(page, ".reading-pane");
        expect(room, "the reading pane must be able to scroll at all").toBeGreaterThan(0);

        // Inside the email document itself. The frame is an opaque origin, so
        // this is where a long email necessarily scrolls -- and it must not
        // reach the list or the document behind it.
        const moved = await scrollEmail(page, 600);
        expect(moved.after).toBeGreaterThan(moved.before);

        // And the pane around it holds its own position.
        await page.locator(".reading-pane").evaluate((node) => {
          node.scrollTop = node.scrollHeight;
        });
        await page.waitForTimeout(150);

        expect(await scrollTop(page, ".reading-pane")).toBe(room);
        expect(await scrollTop(page, ".list-scroll")).toBe(listBefore);
        expect(
          await page.evaluate(() => document.scrollingElement!.scrollTop),
        ).toBe(0);
      });

      shellTest("the reading pane keeps its place per message", async ({ page }) => {
        await openMail(page, options);
        const room = await maxScroll(page, ".reading-pane");
        const parked = Math.round(room / 2);

        await page.locator(".reading-pane").evaluate((node, top) => {
          node.scrollTop = top;
        }, parked);
        await page.waitForTimeout(120);
        expect(await scrollTop(page, ".reading-pane")).toBe(parked);

        // Any other message will do; pinning the assertion to a fixture subject
        // only means editing the fixture breaks a test about scrolling.
        const other = page.locator(".message-row").nth(4);
        await other.click();
        await expect(page.locator(".reading-subject")).not.toHaveText("Revised budget approval");
        // A different message starts at the top.
        expect(await scrollTop(page, ".reading-pane")).toBe(0);

        await page.locator(".message-row").first().click();
        await expect(page.locator(".reading-subject")).toContainText("Revised budget");
        await page.waitForTimeout(150);
        expect(await scrollTop(page, ".reading-pane")).toBe(parked);
      });

      shellTest("240 messages do not push the folder pane or hide the sync status", async ({
        page,
      }) => {
        await openMail(page, { ...options, total: 240 });
        await expect(page.locator(".message-row")).toHaveCount(240);

        const folder = await page.locator(".folder-pane").boundingBox();
        expect(folder!.height).toBeLessThanOrEqual(options.height);
        expect(folder!.height).toBeGreaterThan(options.height * 0.6);

        const status = page.locator(".folder-sync");
        await expect(status).toBeInViewport();
        const box = await status.boundingBox();
        expect(box!.y + box!.height).toBeLessThanOrEqual(options.height + 1);
      });

      shellTest("the collapse control is reachable, labelled and keyboard operable", async ({
        page,
      }) => {
        await openMail(page, { ...options, foldersCollapsed: false });

        const toggle = page.locator(".folder-collapse");
        await expect(toggle).toBeVisible();
        await expect(toggle).toHaveAttribute("aria-label", "Collapse folders");
        await expect(toggle).toHaveAttribute("aria-expanded", "true");

        await toggle.focus();
        await page.keyboard.press("Enter");
        await expect(toggle).toHaveAttribute("aria-expanded", "false");
        await expect(toggle).toHaveAttribute("aria-label", "Expand folders");
        await expect(page.locator(".mail-page")).toHaveAttribute("data-folders", "collapsed");

        await page.keyboard.press("Space");
        await expect(toggle).toHaveAttribute("aria-expanded", "true");
        await expect(page.locator(".mail-page")).toHaveAttribute("data-folders", "expanded");
      });

      shellTest("collapsing gives the space to the list and the reading pane", async ({ page }) => {
        await openMail(page, { ...options, foldersCollapsed: false });

        const wide = (await page.locator(".list-pane").boundingBox())!;
        const readingWide = (await page.locator(".reading-pane").boundingBox())!;
        const railWide = (await page.locator(".folder-pane").boundingBox())!.width;

        await page.locator(".folder-collapse").click();
        // The rail animates; wait for the transition to settle.
        await page.waitForTimeout(400);

        const rail = await page.locator(".folder-pane").boundingBox();
        expect(rail!.width).toBeLessThanOrEqual(64);

        const narrow = (await page.locator(".list-pane").boundingBox())!;
        const reading = (await page.locator(".reading-pane").boundingBox())!;

        // Nothing is taken from the panes: everything the rail gives up is
        // theirs. The list also never gets narrower -- at 1024 it is already at
        // its 300px floor, so it stays put and the reading pane takes the lot.
        expect(narrow.width).toBeGreaterThanOrEqual(wide.width - 0.5);
        expect(reading.width).toBeGreaterThan(readingWide.width);
        expect(narrow.width + reading.width - (wide.width + readingWide.width)).toBeCloseTo(
          railWide - rail!.width,
          0,
        );
      });

      shellTest("the collapsed rail names every folder", async ({ page }) => {
        await openMail(page, { ...options, foldersCollapsed: true });
        await expect(page.locator(".folder-row").first()).toBeVisible();

        const rows = page.locator(".folder-row");
        // System folders with nothing indexed are not offered as dead ends.
        await expect(rows).toHaveCount(FOLDERS.filter((f) => f.total > 0).length);

        const named = await rows.evaluateAll((nodes: Element[]) =>
          nodes.map((node) => ({
            label: node.getAttribute("aria-label"),
            title: node.getAttribute("title"),
            tooltip: node.querySelector(".folder-tooltip")?.textContent ?? null,
          })),
        );
        for (const row of named) {
          expect(row.label, "collapsed rows need an accessible name").toBeTruthy();
          expect(row.title, "collapsed rows need a browser tooltip").toBeTruthy();
          expect(row.tooltip, "collapsed rows need a visible tooltip").toBeTruthy();
        }

        // And the tooltip actually appears on hover.
        const first = rows.first();
        await first.hover();
        await expect(first.locator(".folder-tooltip")).toBeVisible();

        // The unread count is still readable in the rail: a badge that is
        // present in the DOM but not painted is worse than no badge at all.
        const badge = rows.filter({ hasText: "Inbox" }).first().locator(".folder-badge");
        await expect(badge).toBeVisible();
        expect(
          await badge.evaluate((node) => parseFloat(getComputedStyle(node).fontSize)),
        ).toBeGreaterThan(0);
      });

      shellTest("the collapse state survives a reload", async ({ page }) => {
        // No seeded preference here: the point is that whatever the reader does
        // is what the next visit shows. Seeding would be rewritten on reload.
        await openMail(page, options);
        await page.locator(".folder-collapse").click();
        await page.waitForTimeout(400);
        const width = (await page.locator(".folder-pane").boundingBox())!.width;
        const state = await page.locator(".mail-page").getAttribute("data-folders");

        await page.reload();
        await expect(page.locator(".message-row").first()).toBeVisible();
        await page.waitForTimeout(400);

        expect((await page.locator(".folder-pane").boundingBox())!.width).toBeCloseTo(width, 0);
        expect(await page.locator(".mail-page").getAttribute("data-folders")).toBe(state);
      });

      shellTest("every label on the screen meets 4.5:1, expanded and collapsed", async ({
        page,
      }) => {
        await openMail(page, { ...options, foldersCollapsed: false });
        await expectReadable(page, `${viewport.name} ${theme} expanded`);
        await page.screenshot({ path: shot("expanded"), fullPage: false });

        await page.locator(".folder-collapse").click();
        await page.waitForTimeout(400);
        await expectReadable(page, `${viewport.name} ${theme} collapsed`);
        await page.screenshot({ path: shot("collapsed"), fullPage: false });
      });

      shellTest("the email body is readable in both themes, styled or not", async ({ page }) => {
        await openMail(page, options);

        // A plain-text message: themed colours, forced inherit.
        const themed = await emailBodyColours(page);
        expect(themed.background).toBe(
          theme === "light" ? "rgb(239, 237, 237)" : "rgb(28, 26, 27)",
        );
        expect(themed.ratio, "themed email body").toBeGreaterThanOrEqual(4.5);

        // An HTML message that brings its own colours: paper, in both themes.
        await page.locator(".message-row").nth(1).click();
        await expect(page.locator(".reading-subject")).toContainText("Quarterly report");
        await expect(emailFrame(page).locator(".original-formatting")).toHaveText(
          "Original formatting",
        );
        const paper = await emailBodyColours(page);
        expect(paper.background).toBe("rgb(255, 255, 255)");
        expect(paper.color).toBe("rgb(23, 21, 22)");
        expect(paper.ratio, "paper email body").toBeGreaterThanOrEqual(4.5);
        await page.screenshot({ path: shot("paper-email"), fullPage: false });

        // Selection has to be legible too, in whichever theme is active.
        const selection = await emailFrame(page)
          .locator("body")
          .evaluate((node) => {
            const selection = getComputedStyle(node, "::selection");
            return { background: selection.backgroundColor, color: selection.color };
          });
        expect(selection.background).not.toBe("rgba(0, 0, 0, 0)");

        // And a sender-designed dark email: its background survives, its ink
        // does not. This was black-on-black in both themes.
        await page.locator(".message-row").nth(2).click();
        await expect(page.locator(".reading-subject")).toContainText("Delivery rescheduled");
        const dark = await emailBodyColours(page);
        expect(dark.color, "a dark email still needs light ink in the light theme")
          .toBe("rgb(243, 240, 241)");
        expect(dark.background, "and a dark sheet to put it on").toBe("rgb(28, 26, 27)");
        expect(dark.ratio, "dark email body").toBeGreaterThanOrEqual(4.5);

        // Asserted on the email's own content, not just on <body>. A cascade that
        // fails to reach the message renders a perfectly readable body around
        // unreadable text, so checking the body alone proves nothing.
        const painted = await emailFrame(page)
          .locator("table")
          .first()
          .evaluate((node) => ({
            color: getComputedStyle(node).color,
            background: getComputedStyle(node).backgroundColor,
          }));
        expect(painted.background, "the sender's own background is kept").toBe(
          "rgb(20, 20, 25)",
        );
        expect(painted.color, "and their black ink is replaced").toBe("rgb(243, 240, 241)");
        const cell = await emailFrame(page)
          .locator("td")
          .first()
          .evaluate((node) => getComputedStyle(node).color);
        expect(cell, "the override reaches the text itself").toBe("rgb(243, 240, 241)");
        await page.screenshot({ path: shot("dark-email"), fullPage: false });
      });
    });
  }
}

/** Below this width three columns leave the reading pane unreadable. */
test("the rail is the default under 1100px wide", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "the viewport is set inline");
  await stubMailApi(page);
  await page.setViewportSize({ width: 1024, height: 700 });
  await page.goto("/app/mail");
  await expect(page.locator(".message-row").first()).toBeVisible();

  expect((await page.locator(".folder-pane").boundingBox())!.width).toBeLessThanOrEqual(64);
  await expect(page.locator(".mail-page")).toHaveAttribute("data-folders", "collapsed");
});

test("one pane at a time on a narrow screen", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "needs the mobile project");
  await stubMailApi(page);
  await page.goto("/app/mail");
  await expect(page.locator(".message-row").first()).toBeVisible();

  await expect(page.locator(".folder-pane")).toBeHidden();
  await expect(page.locator(".list-pane")).toBeVisible();
  await expect(page.locator(".reading-pane")).toBeHidden();

  await page.locator(".message-row").first().click();
  await expect(page.locator(".reading-pane")).toBeVisible();
  await expect(page.locator(".list-pane")).toBeHidden();
});
