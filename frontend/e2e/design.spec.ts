import { expect, gotoApp, isoAt, test } from "./fixtures";

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
 * The three-way appearance contract, which is easy to get subtly wrong.
 *
 * The default is light, not system: this is read in daylight and every comparable
 * product ships light only, so matching Windows is offered rather than assumed.
 * That means `data-theme="light"` is normally present and the media rule is
 * guarded out — a dark machine does *not* darken the app unless asked.
 *
 * "Match Windows" removes the attribute rather than resolving the preference in
 * JavaScript, so the media rule decides and keeps deciding when the machine
 * switches at sunset, with no listener to maintain.
 */
test("appearance_is_light_by_default_and_follows_windows_only_when_asked", async ({ page }) => {
  // Emulated on the page rather than by opening a context with `colorScheme`:
  // these specs attach to a browser over CDP, where the default context carries
  // none of the config's options, so a context made here would have no baseURL.
  const canvas = () =>
    page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const luminance = (rgb: string) => {
    const [r, g, b] = rgb.match(/\d+(\.\d+)?/g)!.map(Number);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const setTheme = (value: string | null) =>
    page.evaluate((theme) => {
      if (theme === null) document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", theme);
    }, value);

  await page.emulateMedia({ colorScheme: "dark" });
  await gotoApp(page);

  // Default: light, and a dark machine does not change that.
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const byDefault = luminance(await canvas());

  await page.emulateMedia({ colorScheme: "light" });
  expect(luminance(await canvas()), "the default must not track the system").toBe(byDefault);

  // "Match Windows": now it tracks, in both directions.
  await setTheme(null);
  const systemLight = luminance(await canvas());
  await page.emulateMedia({ colorScheme: "dark" });
  const systemDark = luminance(await canvas());
  expect(systemDark, "matching Windows should darken on a dark machine").toBeLessThan(
    systemLight,
  );

  // An explicit light choice still wins on a dark machine.
  await setTheme("light");
  expect(luminance(await canvas())).toBeGreaterThan(systemDark);

  // And an explicit dark choice wins on a light machine.
  await page.emulateMedia({ colorScheme: "light" });
  await setTheme("dark");
  expect(luminance(await canvas())).toBeLessThan(systemLight);
});

/**
 * Typography is scoped to script. The tracking scale in tokens.css is a Latin
 * device: Hebrew running text is never letter-spaced, and tightening it damages
 * word shape rather than tidying it. Applying it on `body`, as this first did,
 * degraded every Hebrew screen while looking perfectly correct in English —
 * which is precisely the class of bug an English-language review cannot catch.
 */
test("hebrew_is_not_letter_spaced_and_latin_is", async ({ page, seed }) => {
  await seed([{ id: "e2e-type", title: "פגישה עם Kubernetes", started_at: isoAt(0, 9) }]);

  await gotoApp(page);
  const latin = await page.evaluate(() => {
    const el = document.querySelector("[data-testid=app]")!;
    return getComputedStyle(el).letterSpacing;
  });
  // A Latin interface keeps the negative tracking.
  expect(latin).not.toBe("normal");
  expect(parseFloat(latin)).toBeLessThan(0);

  await gotoApp(page, "/settings");
  await page.getByTestId("ui-language").selectOption("he");
  await page.getByTestId("nav-timeline").click();

  const hebrew = await page.evaluate(() => {
    const el = document.querySelector("[data-testid=app]")!;
    const style = getComputedStyle(el);
    return {
      dir: document.documentElement.dir,
      letterSpacing: style.letterSpacing,
      lineHeight: parseFloat(style.lineHeight) / parseFloat(style.fontSize),
    };
  });
  expect(hebrew.dir).toBe("rtl");
  expect(hebrew.letterSpacing, "Hebrew must not be tracked").toBe("normal");
  expect(hebrew.lineHeight, "Hebrew wants more leading").toBeGreaterThan(1.6);
});

/**
 * Instruments keep their direction. The level meter colours its segments by
 * index — the hot ones are last — so in a Hebrew interface the flex row reverses
 * and the meter fills from the right with red on the left, reading backwards.
 */
test("level_meters_do_not_mirror_in_hebrew", async ({ page }) => {
  await gotoApp(page, "/settings");
  await page.getByTestId("ui-language").selectOption("he");

  const meter = page.getByTestId("mic-meter-me").locator("[role=meter]");
  await expect(meter).toHaveAttribute("dir", "ltr");

  // The resolved direction, not just the attribute: a parent could still flip it.
  expect(await meter.evaluate((el) => getComputedStyle(el).direction)).toBe("ltr");
});

/**
 * The appearance choice is stored like every other preference, so a reload does
 * not flash back to light. Choosing dark and having the next launch come up white
 * is worse than not offering the choice at all.
 */
test("the_appearance_choice_survives_a_reload", async ({ page }) => {
  await gotoApp(page, "/settings");
  await page.getByTestId("ui-theme").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.reload();
  await expect(page.getByTestId("ui-theme")).toHaveValue("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.getByTestId("ui-theme").selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
