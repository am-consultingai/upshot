/**
 * Screenshots of the real app, for design review.
 *
 * Skipped unless MA_SHOTS is set: it writes files and there is no assertion in
 * it, so it has no business slowing down or reporting on an ordinary run.
 *
 *   MA_SHOTS=1 python3 scripts/windows/e2e.py --grep shots_
 *
 * Output lands in artifacts/, which is gitignored. Worth running before and
 * after any visual change — the unreadable dark-mode summary that stopped the
 * theme being wired to the system preference was found this way and would not
 * have been found by reading the CSS.
 */
import { gotoApp, isoAt, test } from "./fixtures";

const SUMMARY = `<h2>Decisions</h2>
<p>We are moving the ingest pipeline to Kubernetes before the end of the quarter,
and holding the migration of the reporting jobs until after the audit.</p>
<h2>Action items</h2>
<ul><li><strong>Dana</strong> — draft the migration runbook, due Friday</li>
<li><strong>Yoav</strong> — confirm the audit window with the finance team</li></ul>
<h3>Open questions</h3>
<blockquote>Who owns the rollback if the cutover slips past the audit?</blockquote>
<p>See the <a href="#">capacity note</a> for the sizing assumptions.</p>`;

const SEED = [
  {
    id: "shot-1",
    title: "Quarterly planning with the platform team",
    state: "RENDERED",
    started_at: isoAt(0, 95),
    summary_html: SUMMARY,
    turns: [
      { speaker: "THEM", at_ms: 12000, text: "Let's start with the migration timeline." },
      { speaker: "ME", at_ms: 18000, text: "We move ingest first, reporting after the audit." },
    ],
  },
  {
    id: "shot-2",
    title: "Weekly sync",
    state: "RENDERED",
    started_at: isoAt(1, 40),
    summary_html: SUMMARY,
  },
  { id: "shot-3", title: "Client call — onboarding", state: "TRANSCRIBED", started_at: isoAt(2, 25) },
];

for (const theme of ["light", "dark"] as const) {
  test(`shots_${theme}`, async ({ page, seed }) => {
    test.skip(!process.env.MA_SHOTS, "set MA_SHOTS=1 to capture screenshots");
    await seed(SEED);
    await page.setViewportSize({ width: 1280, height: 900 });
    for (const [name, path] of [
      ["timeline", "/"],
      ["meeting", "/m/shot-1"],
      ["settings", "/settings"],
    ] as const) {
      await gotoApp(page, path);
      await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
      await page.evaluate(async () => {
        await document.fonts.ready;
      });
      await page.waitForTimeout(400);
      await page.screenshot({ path: `../artifacts/shot-${name}-${theme}.png`, fullPage: false });
    }
  });
}
