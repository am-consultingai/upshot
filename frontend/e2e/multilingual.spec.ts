import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, test } from "./fixtures";

/**
 * A meeting in any Whisper language runs the way its language is written, while the
 * interface stays in the interface language (the multilingual epic, story D). The
 * direction comes from the API, not a list in the page.
 *
 * Screenshots for review go to artifacts/multilingual when UP_SHOTS is set.
 */

const OUT = "../artifacts/multilingual";
const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

const MEETINGS = {
  ar: {
    title: "مراجعة المشروع",
    text: "لدينا مشكلة في النشر على بيئة الإنتاج",
    summary: "<h2>ملخص</h2><p>اتفقنا على تأجيل الإصدار إلى الأسبوع القادم.</p>",
    dir: "rtl",
  },
  es: {
    title: "Revisión del proyecto",
    text: "tenemos un problema con el despliegue en producción",
    summary: "<h2>Resumen</h2><p>Acordamos mover el lanzamiento a la semana que viene.</p>",
    dir: "ltr",
  },
  zh: {
    title: "项目评审",
    text: "生产环境的部署有问题",
    summary: "<h2>摘要</h2><p>我们同意把发布推迟到下周。</p>",
    dir: "ltr",
  },
} as const;

for (const ui of ["en", "he"] as const) {
  for (const [language, m] of Object.entries(MEETINGS)) {
    test(`a_${language}_meeting_runs_${m.dir}_with_a_${ui}_interface`, async ({ page, seed, request }) => {
      await seed([
        {
          id: `ml-${language}`,
          title: m.title,
          state: "RENDERED",
          started_at: isoAt(0, 10),
          language,
          summary_html: m.summary,
          turns: [{ speaker: "THEM", at_ms: 1000, text: m.text }],
        },
      ]);
      await request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.language": ui } } });
      await page.setViewportSize({ width: 1280, height: 800 });
      await gotoApp(page, `/m/ml-${language}`);

      // The notes run the meeting's way; the page around them the interface's.
      const summary = page.getByTestId("summary-html");
      await expect(summary).toHaveAttribute("dir", m.dir);
      await expect(page.locator("html")).toHaveAttribute("dir", ui === "he" ? "rtl" : "ltr");
      await expect(page.locator("html")).toHaveAttribute("lang", ui);
      if (process.env.UP_SHOTS) await page.screenshot({ path: `${OUT}/${language}-summary-ui-${ui}.png` });

      await page.getByTestId("meeting-tab-transcript").click();
      await expect(page.getByTestId("transcript")).toHaveAttribute("dir", m.dir);
      await expect(page.getByTestId("transcript")).toContainText(m.text);
      if (process.env.UP_SHOTS) await page.screenshot({ path: `${OUT}/${language}-transcript-ui-${ui}.png` });
    });
  }
}
