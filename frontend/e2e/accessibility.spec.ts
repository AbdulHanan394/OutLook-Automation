import AxeBuilder from "@axe-core/playwright";
import { expect, openMail, test } from "./fixtures";

for (const theme of ["light", "dark"] as const) {
  test(`mail client has no axe accessibility violations in ${theme} theme`, async ({
    signedIn: page,
  }) => {
    await page.addInitScript((value) => {
      localStorage.setItem("email-finder-theme", value);
    }, theme);
    await openMail(page);
    await expect(page.locator(".message-group").first()).toBeVisible();

    const results = await new AxeBuilder({ page }).analyze();
    for (const violation of results.violations) {
      console.log(
        `[axe:${theme}] ${violation.impact} ${violation.id}: ${violation.nodes.length} affected nodes`,
      );
    }

    expect(results.violations).toEqual([]);
  });
}
