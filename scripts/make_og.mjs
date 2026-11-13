// Render the link-preview images (og:image) from the hero: same photo, same headline.
//
//     node scripts/make_og.mjs
//
// Writes site/og.jpg (English) and site/og-he.jpg (Hebrew, the photo mirrored the way the
// Hebrew hero mirrors it), 1200x630, both committed. JPEG, because WhatsApp drops previews
// much over 300 KB. Uses the Playwright that the frontend already installs
// (cd frontend && npm ci && npx playwright install chromium).
// Run it again whenever the hero photo or headline changes.

import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SITE = join(ROOT, "site");
const { chromium } = createRequire(join(ROOT, "frontend", "package.json"))("@playwright/test");

// the hero's own headline, read from the page so the two never drift apart
const headline = (page) => {
  const found = readFileSync(join(SITE, page), "utf8").match(/<h1\b[^>]*>(.*?)<\/h1>/s);
  if (!found) throw new Error(`no <h1> in site/${page}`);
  return found[1];
};

const PAGES = [
  {
    out: "og.jpg",
    lang: "en",
    dir: "ltr",
    eyebrow: "Meeting notes for Windows",
    h1: headline("index.html"),
    cta: "Download for Windows",
    meta: "No bot in the meeting · audio stays on your PC",
  },
  {
    out: "og-he.jpg",
    lang: "he",
    dir: "rtl",
    eyebrow: "סיכומי פגישות ל-Windows",
    h1: headline("he/index.html"),
    cta: "הורדה ל-Windows",
    meta: "בלי בוט בפגישה · האודיו נשאר על המחשב",
  },
];

const html = (p) => `<!doctype html>
<html lang="${p.lang}" dir="${p.dir}">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600&family=Inter:wght@400;500&family=JetBrains+Mono:wght@500;600&family=Rubik:wght@500;600&family=Heebo:wght@400;500;600&display=block">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/am-consultingai/am-assets@v2/brand.css">
<link rel="stylesheet" href="${pathToFileURL(join(SITE, "site.css"))}">
<style>
  html,body{margin:0;width:1200px;height:630px;overflow:hidden;background:var(--bg)}
  .og{position:relative;width:1200px;height:630px;overflow:hidden}
  .og > img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center}
  [dir=rtl] .og > img{transform:scaleX(-1)}
  .og::after{content:"";position:absolute;inset:0;
    background:linear-gradient(90deg,rgba(7,11,19,.97) 0%,rgba(7,11,19,.9) 38%,rgba(7,11,19,.45) 60%,rgba(7,11,19,0) 80%)}
  [dir=rtl] .og::after{background:linear-gradient(270deg,rgba(7,11,19,.97) 0%,rgba(7,11,19,.9) 38%,rgba(7,11,19,.45) 60%,rgba(7,11,19,0) 80%)}
  .in{position:relative;z-index:1;height:100%;box-sizing:border-box;padding:56px 64px;
    display:flex;flex-direction:column;justify-content:space-between;max-width:700px}
  .mark{display:flex;align-self:flex-start;align-items:center;gap:14px;font-family:"Space Grotesk",sans-serif;
    font-size:34px;font-weight:600;color:var(--ink);direction:ltr}
  .mark img{width:52px;height:52px}
  .mark span{color:var(--s3);font-weight:500}
  .eyebrow{margin:0}
  h1{font-size:64px;line-height:1.02;letter-spacing:-.035em;margin:16px 0 0;max-width:12ch}
  [lang=he] h1{font-size:60px;line-height:1.12}
  .btn{margin:30px 0 0 !important;align-self:flex-start;font-size:20px;padding:16px 28px;border-radius:12px}
  .hero-meta{margin-top:0;font-size:15px}
</style>
</head>
<body>
<div class="og">
  <img src="${pathToFileURL(join(SITE, "img", "hero.webp"))}" alt="">
  <div class="in">
    <div class="mark"><img src="${pathToFileURL(join(SITE, "img", "upshot-mark.svg"))}" alt=""><div><span>up</span>shot</div></div>
    <div>
      <p class="eyebrow">${p.eyebrow}</p>
      <h1>${p.h1}</h1>
      <span class="btn btn-pri">${p.cta}</span>
    </div>
    <p class="hero-meta">${p.meta}</p>
  </div>
</div>
</body>
</html>`;

const tmp = mkdtempSync(join(tmpdir(), "upshot-og-"));
// CHROME=<path to a Chrome> picks a browser other than Playwright's own
const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  for (const p of PAGES) {
    // a file page, so it may load the site's local photo, mark and stylesheet
    const file = join(tmp, `${p.lang}.html`);
    writeFileSync(file, html(p));
    await page.goto(pathToFileURL(file).href, { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: join(SITE, p.out), type: "jpeg", quality: 86 });
    console.log(`wrote site/${p.out}`);
  }
} finally {
  await browser.close();
  rmSync(tmp, { recursive: true, force: true });
}
