import { expect, gotoApp, test } from "./fixtures";

/**
 * The design foundation: bundled fonts, derived colour tokens, and a dark theme
 * that follows from the same three inputs rather than a second hand-kept palette.
 *
 * These assert computed values in a real browser because every mechanism here can
 * fail silently. A missing @font-face falls back to a system face that looks
 * plausible; an unresolved custom property computes to nothing and inherits; a
 * `color-mix()` the engine dislikes is simply dropped. In each case the page still
 * renders, just not as designed — which is exactly how the summary went unstyled
 * for as long as it did.
 */

test("bundled_fonts_load_rather_than_falling_back", async ({ page }) => {
  await gotoApp(page);

  // The UI face is in use on every page, so it must already be loaded.
  const ui = await page.evaluate(async () => {
    await document.fonts.ready;
    return document.fonts.check('400 14px "Heebo"');
  });
  expect(ui, "Heebo should be loaded, not substituted").toBe(true);

  // The display face is loaded lazily and correctly so: `unicode-range` means the
  // browser fetches a subset only when something actually renders with it, and on
  // a page with no display type that is nothing. So request it explicitly — which
  // is the real assertion anyway, that the bundled file exists and parses.
  const display = await page.evaluate(async () => {
    try {
      const faces = await document.fonts.load('700 24px "Frank Ruhl Libre"', "סיכום Summary");
      return faces.length > 0;
    } catch {
      return false;
    }
  });
  expect(display, "Frank Ruhl Libre should be bundled and loadable").toBe(true);

  // Nothing may be fetched from a font CDN: this app runs offline.
  const external = await page.evaluate(() =>
    [...document.querySelectorAll("link[rel=stylesheet], link[rel=preconnect]")]
      .map((el) => el.getAttribute("href") ?? "")
      .filter((href) => /fonts\.(googleapis|gstatic)\.com/.test(href)),
  );
  expect(external, "fonts must be bundled, not fetched").toEqual([]);

  expect(
    await page.evaluate(() => getComputedStyle(document.body).fontFamily),
  ).toContain("Heebo");
});

test("colour_tokens_derive_from_the_three_inputs", async ({ page }) => {
  await gotoApp(page);

  const resolved = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const read = (name: string) => root.getPropertyValue(name).trim();
    return {
      base: read("--base"),
      ink: read("--ink"),
      accent: read("--accent"),
      surface1: read("--surface-1"),
      surface2: read("--surface-2"),
      textSecondary: read("--text-secondary"),
      border: read("--border"),
      bodyBackground: getComputedStyle(document.body).backgroundColor,
    };
  });

  // The inputs exist.
  expect(resolved.base).not.toBe("");
  expect(resolved.ink).not.toBe("");
  expect(resolved.accent).not.toBe("");

  // And the derived values resolved to real colours rather than being dropped,
  // which is what happens when an engine rejects the color-mix().
  for (const [name, value] of Object.entries({
    surface1: resolved.surface1,
    surface2: resolved.surface2,
    textSecondary: resolved.textSecondary,
    border: resolved.border,
  })) {
    expect(value, `${name} should resolve to a colour`).toMatch(/(rgb|oklab|color|#)/);
  }

  // The canvas is the warm off-white, not the browser's white.
  expect(resolved.bodyBackground).not.toBe("rgb(255, 255, 255)");
  expect(resolved.bodyBackground).toMatch(/^rgba?\(/);
});

test("dark_theme_redefines_the_inputs_and_drops_shadows", async ({ page }) => {
  await gotoApp(page);

  const light = await page.evaluate(() => ({
    canvas: getComputedStyle(document.body).backgroundColor,
    shadow: getComputedStyle(document.documentElement).getPropertyValue("--shadow-md").trim(),
  }));

  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));

  const dark = await page.evaluate(() => ({
    canvas: getComputedStyle(document.body).backgroundColor,
    shadow: getComputedStyle(document.documentElement).getPropertyValue("--shadow-md").trim(),
    hairline: getComputedStyle(document.documentElement).getPropertyValue("--hairline").trim(),
  }));

  expect(dark.canvas, "the canvas must change with the theme").not.toBe(light.canvas);

  // Light mode carries a real shadow; dark mode does elevation with a hairline
  // instead, because shadows on a dark canvas read as smudges.
  expect(light.shadow).not.toBe("none");
  expect(dark.shadow).toBe("none");
  expect(dark.hairline).toContain("inset");

  // Light is darker text on lighter ground; dark inverts that. Compare luminance
  // of the canvas rather than trusting the literal values.
  const luminance = (rgb: string) => {
    const [r, g, b] = rgb.match(/\d+(\.\d+)?/g)!.map(Number);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  expect(luminance(dark.canvas)).toBeLessThan(luminance(light.canvas));
});

/**
 * Dark mode is built but not yet bound to the system preference, because the
 * components still hardcode Tailwind palette colours that do not move with the
 * theme — a themed token layer over unthemed components renders near-white text
 * on a still-light background, which is how the summary became invisible in the
 * screenshot that prompted this guard.
 *
 * This test holds that line. It must be changed, not deleted, by whoever
 * finishes the component migration and turns the media query on.
 */
test("a_dark_system_does_not_yet_flip_the_app", async ({ page }) => {
  // Emulated on the page rather than by opening a context with `colorScheme`:
  // these specs attach to a browser over CDP, where the default context carries
  // none of the config's options, so a context made here would have no baseURL.
  await page.emulateMedia({ colorScheme: "dark" });
  await gotoApp(page);

  const onDarkSystem = await page.evaluate(() => ({
    canvas: getComputedStyle(document.body).backgroundColor,
    text: getComputedStyle(document.body).color,
  }));

  await page.emulateMedia({ colorScheme: "light" });
  const onLightSystem = await page.evaluate(() => ({
    canvas: getComputedStyle(document.body).backgroundColor,
    text: getComputedStyle(document.body).color,
  }));

  expect(
    onDarkSystem,
    "the system preference must not select the theme until components are migrated",
  ).toEqual(onLightSystem);

  // And the text stays dark-on-light, which is the property that actually broke.
  const luminance = (rgb: string) => {
    const [r, g, b] = rgb.match(/\d+(\.\d+)?/g)!.map(Number);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  expect(luminance(onDarkSystem.text)).toBeLessThan(luminance(onDarkSystem.canvas));
});
