import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * A summary is HTML a model wrote. The allowlist keeps `position: relative`, offsets and
 * transforms, so a box could still be slid out of it, over the app's own controls (left open by
 * the PR #13 review, z8tj1hftg0). Nothing inside the summary paints outside it.
 */
test("a_summary_cannot_paint_over_the_apps_controls", async ({ page, seed }) => {
  await seed([
    {
      id: "m-sliding",
      title: "Sliding box",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html:
        '<p>Notes.</p><div style="position:relative;top:-600px;' +
        'left:-600px;width:3000px;height:1200px;z-index:9999;background:#f00">over</div>',
      turns: [{ speaker: "THEM", at_ms: 1000, text: "Hello." }],
    },
  ]);
  await gotoApp(page, "/m/m-sliding");
  const slider = page.getByTestId("summary-html").locator("div", { hasText: "over" });
  // The allowlist keeps the offset: this is what the page has to contain.
  await expect(slider).toHaveCSS("position", "relative");
  // A point just outside the summary, beside it: the box must not be what is there.
  const summary = (await page.getByTestId("summary-html").boundingBox())!;
  const beside = await page.evaluate(
    ([x, y]) => document.elementFromPoint(x, y)?.textContent === "over",
    [summary.x - 12, summary.y + 4],
  );
  expect(beside, "the summary's box paints outside the summary").toBe(false);
});
