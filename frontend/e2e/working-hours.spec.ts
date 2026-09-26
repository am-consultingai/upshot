/**
 * Working hours (Settings, Calendar): 08:00–18:00 unless changed, and the day and week
 * views shade the hours outside them.
 */
import { expect, gotoApp, gotoSettings, reset, test } from "./fixtures";

/** 0.8 px a minute, as TimeGrid draws it. */
const PX_PER_HOUR = 48;

test.afterEach(async ({ request }) => {
  await reset(request);
});

test("working_hours_default_to_eight_till_six_and_shade_the_week", async ({ page, seed }) => {
  await seed([]);
  await gotoSettings(page, "calendar");
  await expect(page.getByTestId("work-start")).toHaveValue("8");
  await expect(page.getByTestId("work-end")).toHaveValue("18");

  await gotoApp(page, "/");
  await page.getByTestId("span-week").click();
  const shade = page.locator("[data-testid=calendar-daybody]").first().getByTestId("off-hours");
  await expect(shade).toHaveCount(2);
  expect((await shade.first().boundingBox())!.height).toBeCloseTo(8 * PX_PER_HOUR, 0);
});

test("changing_them_saves_and_moves_the_shading", async ({ page, seed }) => {
  await seed([]);
  await gotoSettings(page, "calendar");
  await page.getByTestId("work-start").selectOption("9");
  await page.getByTestId("work-end").selectOption("17");
  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.calendar)
    .toMatchObject({ work_start: 9, work_end: 17 });
  // Only hours that keep the day running forwards are offered.
  await expect(page.getByTestId("work-end").locator("option[value='9']")).toHaveCount(0);

  await gotoApp(page, "/");
  await page.getByTestId("span-week").click();
  const shade = page.locator("[data-testid=calendar-daybody]").first().getByTestId("off-hours");
  expect((await shade.first().boundingBox())!.height).toBeCloseTo(9 * PX_PER_HOUR, 0);
});
