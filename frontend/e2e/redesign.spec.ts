/**
 * The redesign mock, item by item.
 *
 * Each test here is one line of the ticket that catalogued what the app still did
 * differently from `.ui-research/mocks/redesign.html` — seeded with the mock's own
 * library (parity-data.ts), so what is asserted is what the mock draws: the rail's
 * "Up next", the all-day band, "1 of 3 done", named speakers, related meetings with
 * their reasons, and so on. A test failing here is the gap reopening.
 */
import { CSRF, SESSION } from "../playwright.config";
import { dateFromToday, expect, gotoApp, test } from "./fixtures";
import { parityBody } from "./parity-data";

test.beforeEach(async ({ seedBody, request }) => {
  await seedBody(parityBody());
  // The first-run capture question is answered once in real life; answer it here too,
  // so it is not pushing every screen down by 220px. And the model is the fake one: the
  // provider specs leave a real provider selected, and the reset does not restore it —
  // Ask this meeting then waits on a server that is not there.
  await request.put("/api/settings", {
    headers: { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` },
    data: { values: { "detection.decided": true, "llm.provider": "fake" } },
  });
});

// ------------------------------------------------------------------- library

test("library_top_bar_reads_month_year_arrows_t_and_week_number", async ({ page }) => {
  await gotoApp(page, "/");
  const period = page.getByTestId("calendar-period");
  const month = new Intl.DateTimeFormat("en", { month: "long" }).format(new Date());
  await expect(period.locator("b")).toHaveText(month);
  await expect(period).toContainText(String(new Date().getFullYear()));
  await expect(page.getByTestId("calendar-today")).toHaveText("T");
  await expect(page.getByTestId("calendar-week")).toHaveText(/^W\d{1,2}$/);
  for (const span of ["day", "week", "month", "list"]) {
    await expect(page.getByTestId(`span-${span}`)).toBeVisible();
  }

  // T is the shortcut the button teaches: page away, press T, come back.
  const week = await page.getByTestId("calendar-week").textContent();
  await page.getByTestId("calendar-next").click();
  await expect(page.getByTestId("calendar-week")).not.toHaveText(week ?? "");
  await page.locator("body").press("t");
  await expect(page.getByTestId("calendar-week")).toHaveText(week ?? "");
});

test("day_headers_stack_weekday_over_date_with_today_in_a_pill", async ({ page }) => {
  await gotoApp(page, "/");
  const today = page.locator("[data-testid=calendar-daycolumn][data-today=true]");
  await expect(today).toHaveCount(1);
  await expect(today).toContainText(String(new Date().getDate()));
  await expect(today).toContainText(new Intl.DateTimeFormat("en", { weekday: "short" }).format(new Date()));
  // The zone the gutter is in heads it.
  await expect(page.getByTestId("calendar-zone")).not.toBeEmpty();
});

test("all_day_events_get_their_own_band_and_tint_their_day", async ({ page }) => {
  await gotoApp(page, "/");
  // Tomorrow may be next week; step there if so.
  const tomorrow = dateFromToday(1);
  if ((await page.locator(`[data-testid=calendar-daycolumn][data-day="${tomorrow}"]`).count()) === 0) {
    await page.getByTestId("calendar-next").click();
  }
  const band = page.getByTestId("calendar-allday-band");
  await expect(band).toContainText("all-day");
  await expect(band.getByTestId("calendar-allday")).toHaveText("Ron — annual leave");
  // Not stacked inside the date header any more.
  await expect(page.getByTestId("calendar-daycolumn").getByTestId("calendar-allday")).toHaveCount(0);
  await expect(page.locator(`[data-testid=calendar-daybody][data-day="${tomorrow}"]`)).toHaveAttribute(
    "data-tinted",
    "true",
  );
});

test("chips_show_a_time_range_and_what_went_wrong", async ({ page }) => {
  await gotoApp(page, "/");
  // Past days of this week hold the seeded meetings, unless today is early in the week.
  const recorded = page.locator("[data-testid=calendar-gevent][data-event=ev-appsflyer]");
  if ((await recorded.count()) === 0) await page.getByTestId("calendar-prev").click();
  await expect(recorded.getByTestId("chip-when")).toHaveText("15:00 – 16:00");
  await expect(recorded).toHaveAttribute("data-kind", "recorded");
  const failed = page.locator("[data-testid=calendar-event][data-meeting=m-dana]");
  await expect(failed).toHaveAttribute("data-kind", "failed");
  await expect(failed.getByTestId("chip-when")).toHaveText("13:00 · failed");
});

test("a_recorded_chip_carries_a_balloon_of_open_items_and_answers_the_pointer", async ({ page }) => {
  await gotoApp(page, "/");
  const onboarding = page.locator("[data-testid=calendar-gevent][data-event=ev-onboarding]");
  if ((await onboarding.count()) === 0) await page.getByTestId("calendar-prev").click();
  // Two of its three items are still open.
  await expect(onboarding.getByTestId("chip-open")).toHaveText("2");
  // Every item on the standup is done, so it has nothing to flag.
  await expect(page.locator("[data-testid=calendar-event][data-meeting=m-standup]").getByTestId("chip-open")).toHaveCount(0);

  // Hovering a chip visibly marks it: the fill changes, not just an invisible shadow.
  const fill = () => onboarding.evaluate((node) => getComputedStyle(node).backgroundColor);
  await page.mouse.move(5, 895);
  const rest = await fill();
  await onboarding.hover();
  await expect.poll(fill).not.toBe(rest);

  // So does a scheduled one.
  const scheduled = page.locator("[data-testid=calendar-gevent][data-recorded=false]").first();
  const before = await scheduled.evaluate((node) => getComputedStyle(node).backgroundColor);
  await scheduled.hover();
  await expect.poll(() => scheduled.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(before);

  // The month view carries the same balloon.
  await page.getByTestId("span-month").click();
  await expect(page.locator("[data-testid=calendar-monthgrid] [data-testid=chip-open]").first()).toBeVisible();
});

test("the_list_view_is_the_month_as_an_agenda", async ({ page }) => {
  await gotoApp(page, "/");
  await page.getByTestId("span-list").click();
  await expect(page.getByTestId("calendar-agenda")).toBeVisible();
  await expect(page.getByTestId("agenda-day").first()).toBeVisible();
  await expect(page.getByTestId("agenda-row").filter({ hasText: "Onboarding funnel review" })).toHaveCount(1);
  // Remembered like the other views.
  await page.reload();
  await expect(page.getByTestId("calendar-agenda")).toBeVisible();
});

test("the_calendar_rail_has_up_next_and_open_items", async ({ page }) => {
  await gotoApp(page, "/");
  const next = page.getByTestId("rail-up-next");
  await expect(next).toContainText("Vendor call — Wix");
  await expect(page.getByTestId("rail-up-next-when")).toHaveText(/^in 2h 3\dm$/);
  await expect(next).toContainText("3 guests");
  const counts = page.getByTestId("rail-open-count");
  await expect(counts.filter({ hasText: "overdue" })).toBeVisible();
  await expect(counts.filter({ hasText: "no date" })).toBeVisible();

  // "Record this one" starts a recording already matched to that event.
  await page.getByTestId("rail-record-this").click();
  await expect(page.getByTestId("rail-now-recording")).toContainText("Vendor call — Wix");
  await page.getByTestId("rail-stop").click();
  await expect(page.getByTestId("rail-now-recording")).toHaveCount(0);

  // An open-items count goes to that group in the inbox.
  await counts.filter({ hasText: "overdue" }).click();
  await expect(page).toHaveURL(/\/actions#overdue$/);
  await expect(page.locator("[data-bucket-group=overdue]")).toBeVisible();
});

// ------------------------------------------------------------------- sidebar

test("sidebar_rows_say_time_length_and_items_and_the_record_button_says_so", async ({ page }) => {
  await gotoApp(page, "/");
  await expect(page.getByTestId("start-recording")).toContainText("Record a meeting");
  await expect(page.getByTestId("workspace-chevron")).toBeVisible();
  const row = page.locator("[data-testid=meeting-card][data-meeting-id=m-onboarding]");
  await expect(row).toContainText("09:30 · 42m · 2 items");
  await expect(page.locator("[data-testid=meeting-card][data-meeting-id=m-dana]")).toContainText(
    "13:00 · summary failed",
  );
  await expect(page.locator("[data-testid=meeting-card][data-meeting-id=m-standup]")).toContainText("done");
  // Day groups read "Sunday 20 Sep", not "Sunday, Sep 20".
  const header = page.getByTestId("timeline-day").nth(1).locator("h2");
  await expect(header).toHaveText(/^[A-Z][a-z]+day \d{1,2} [A-Z][a-z]{2}$|^Yesterday$/);
  // The footer says what is on this disk.
  await expect(page.getByTestId("sidebar-storage")).toHaveText(/ · [\d.]+ (KB|MB|GB)/);
});

test("a_row_menu_on_hover_and_right_click_deletes_behind_a_real_dialog", async ({ page }) => {
  await gotoApp(page, "/");
  const row = page.locator("[data-testid=meeting-card][data-meeting-id=m-exec]");
  // Revealed into its slot on hover, not always there.
  const slot = row.locator(".ma-slot");
  await expect(slot).toHaveCSS("opacity", "0");
  await row.hover();
  await expect(slot).toHaveCSS("opacity", "1");

  // Right-click opens the same menu.
  await row.click({ button: "right" });
  const menu = page.getByTestId("context-menu");
  await expect(menu).toBeVisible();
  await expect(menu.locator("[data-item=delete]")).toBeVisible();
  await menu.locator("[data-item=delete]").click();

  // A dialog that names the meeting, with Cancel first.
  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toContainText("Exec staff meeting");
  await expect(page.getByTestId("confirm-cancel")).toBeFocused();
  await page.getByTestId("confirm-cancel").click();
  await expect(row).toHaveCount(1);

  await row.hover();
  await row.getByTestId("meeting-menu").click();
  await page.locator("[data-testid=overflow-menu-item][data-item=delete]").click();
  await page.getByTestId("confirm-ok").click();
  await expect(row).toHaveCount(0);
  await expect(page.getByTestId("toast")).toContainText("Deleted “Exec staff meeting”");
});

test("the_brand_row_opens_a_workspace_menu_and_search_has_a_tooltip", async ({ page }) => {
  await gotoApp(page, "/");
  await page.getByTestId("app-title").click();
  const menu = page.getByTestId("workspace-menu");
  await expect(menu).toBeVisible();
  await menu.locator("[data-item=theme]").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.getByTestId("nav-search-icon").hover();
  await expect(page.getByTestId("tooltip").first()).toHaveText(/Search/);
  await page.getByTestId("nav-search-icon").click();
  await expect(page.getByTestId("command-palette")).toBeVisible();
});

// ------------------------------------------------------------------- meeting

test("meeting_chips_carry_date_length_people_and_tags", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  await expect(page.getByTestId("chip-when")).toHaveText(/^[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}$/);
  await expect(page.getByTestId("chip-duration")).toHaveText("42m");
  await expect(page.getByTestId("chip-people")).toHaveText("2 people");
  await expect(page.getByTestId("chip-tag")).toHaveText("Growth");

  // + Add tag writes one, and it is there after a reload.
  await page.getByTestId("chip-add-tag").click();
  await page.getByTestId("tag-input").fill("Roadmap");
  await page.getByTestId("tag-input").press("Enter");
  await expect(page.getByTestId("chip-tag")).toHaveCount(2);
  await page.reload();
  await expect(page.locator("[data-testid=chip-tag][data-tag=Roadmap]")).toBeVisible();
  await page.locator("[data-testid=chip-tag][data-tag=Roadmap]").hover();
  await page.locator("[data-testid=chip-tag][data-tag=Roadmap]").getByTestId("chip-tag-remove").click();
  await expect(page.getByTestId("chip-tag")).toHaveCount(1);

  // No calendar strip for a settled match; the bar says "Copy summary".
  await expect(page.getByTestId("meeting-calendar")).toHaveCount(0);
  await expect(page.getByTestId("copy-summary-bar")).toHaveText("Copy summary");
  // And the rail has no "This meeting" block of When / Open items.
  await expect(page.getByTestId("meeting-rail").locator("h2", { hasText: /^This meeting$/ })).toHaveCount(0);
  await expect(page.getByTestId("meeting-rail")).not.toContainText("Open items");
});

test("action_items_read_n_of_m_done_and_can_be_added_and_folded", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("1 of 3 done");
  const late = page.getByTestId("action-item").filter({ hasText: "drop-off by segment" });
  await expect(late.getByTestId("action-due")).toHaveText("2 days late");
  await expect(late.getByTestId("action-detail")).toHaveText("Blocks the rollback decision");

  await page.getByTestId("meeting-actions-add").click();
  await page.getByTestId("meeting-actions-input").fill("Book the design review");
  await page.getByTestId("meeting-actions-input").press("Enter");
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("1 of 4 done");

  await page.getByTestId("meeting-actions-collapse").click();
  await expect(page.getByTestId("meeting-actions").getByTestId("action-item")).toHaveCount(0);
  await page.getByTestId("meeting-actions-collapse").click();
  await expect(page.getByTestId("meeting-actions").getByTestId("action-item")).toHaveCount(4);
});

test("the_summary_opens_with_a_lead_and_a_next_step_files_itself", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  const lead = page.locator("[data-testid=summary-html] > p").first();
  await expect(lead).toContainText("Activation is down 6%");
  await expect(lead).toHaveCSS("font-size", "20px");
  await expect(page.locator("[data-testid=summary-html] > :first-child")).toHaveJSProperty("tagName", "P");

  // The minimap maps the three sections.
  await expect(page.getByTestId("minimap-section")).toHaveCount(3);
  await page.getByTestId("minimap-section").nth(2).click();
  await expect(page.getByTestId("minimap-section").nth(2)).toHaveAttribute("data-active", "true");

  // The next step under a decision opens the add row with its words in it.
  await page.locator("[data-testid=summary-html] .next").click();
  await expect(page.getByTestId("meeting-actions-input")).toHaveValue("Ask Dana for a timeline to the spec landing");
  await page.getByTestId("meeting-actions-input").press("Enter");
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("1 of 4 done");
});

test("in_the_room_names_the_speakers_with_real_talk_time", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  const people = page.getByTestId("talk-share");
  await expect(people).toHaveCount(3);
  await expect(people.nth(0)).toContainText("You");
  await expect(page.getByTestId("rail-people")).toContainText("Dana Levi");
  await expect(page.getByTestId("rail-people")).toContainText("Yoni Bar");
  // Seconds from each segment's start and end: Dana 14+17+16+17 = 64 of 100.5 ≈ 64%.
  await expect(people.filter({ hasText: "Dana Levi" })).toContainText("64%");

  // A slot can be named, and the name sticks.
  await people.filter({ hasText: "Yoni Bar" }).getByTestId("speaker-name").click();
  await page.getByTestId("speaker-name-input").fill("Yoni Barak");
  await page.getByTestId("speaker-name-input").press("Enter");
  await page.reload();
  await expect(page.getByTestId("rail-people")).toContainText("Yoni Barak");
});

test("related_meetings_say_why", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  const related = page.getByTestId("rail-related");
  await expect(related.first()).toBeVisible();
  // The Q4 session shares an action item with this one, word for word.
  await expect(related.filter({ hasText: "Q4 roadmap working session" })).toContainText(/shares/);
  await expect(page.getByTestId("rail-related-reason").first()).not.toBeEmpty();
});

test("ask_this_meeting_answers_with_a_moment_to_play", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  await expect(page.getByTestId("ask-scope")).toHaveAttribute("data-scope", "meeting");
  await page.getByTestId("ask-scope-trigger").click();
  await page.locator("[data-testid=overflow-menu-item][data-item=related]").click();
  await expect(page.getByTestId("ask-scope")).toHaveAttribute("data-scope", "related");
  await page.getByTestId("ask-scope-trigger").click();
  await page.locator("[data-testid=overflow-menu-item][data-item=meeting]").click();

  await page.getByTestId("ask-input").fill("Who will pull the drop-off by segment?");
  await page.getByTestId("ask-input").press("Enter");
  await expect(page.getByTestId("ask-answer")).toContainText("drop-off by segment");
  const citation = page.getByTestId("ask-citation").first();
  await expect(citation).toHaveText("(00:01:15)");
  await citation.click();
  await expect(page.getByTestId("meeting-tab-transcript")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("audio-player")).toBeVisible();
});

// ------------------------------------------------------------------- transcript

test("the_transcript_has_named_blocks_chapters_and_a_banded_waveform", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  await page.getByTestId("meeting-tab-transcript").click();
  const speakers = page.getByTestId("turn-speaker");
  await expect(speakers.first()).toHaveText(/Dana Levi/);
  await expect(page.getByTestId("transcript-turn").first()).toHaveText("(00:00:04)");

  const chapters = page.getByTestId("rail-chapter");
  await expect(chapters).toHaveCount(4);
  await expect(chapters.nth(0)).toContainText("The drop-off");
  await expect(chapters.nth(0)).toContainText("00:00 – 00:41");
  await expect(page.getByTestId("meeting-rail")).toContainText("Speakers");

  // A band per turn, coloured by who was speaking.
  await expect(page.getByTestId("waveform-band")).toHaveCount(7);
  const colours = await page.getByTestId("waveform-band").evaluateAll((nodes) =>
    [...new Set(nodes.map((node) => getComputedStyle(node).backgroundColor))].length,
  );
  expect(colours).toBe(3);
  await expect(page.getByTestId("transport-time")).toContainText("00:00:00 /");
});

test("find_in_transcript_counts_n_of_m_and_steps_through", async ({ page }) => {
  await gotoApp(page, "/m/m-onboarding");
  await page.keyboard.press("Control+f");
  const input = page.getByTestId("transcript-find-input");
  await expect(input).toBeFocused();
  await input.fill("step three");
  await expect(page.getByTestId("transcript-find-count")).toHaveText("1 of 3");
  // Nothing is filtered away: every turn is still there, the matches marked.
  await expect(page.getByTestId("transcript-block")).toHaveCount(7);
  await expect(page.getByTestId("transcript-match")).toHaveCount(3);
  await input.press("Enter");
  await expect(page.getByTestId("transcript-find-count")).toHaveText("2 of 3");
  await expect(page.locator("[data-testid=transcript-match][data-current=true]")).toHaveAttribute("data-match-index", "1");
  await input.press("Shift+Enter");
  await expect(page.getByTestId("transcript-find-count")).toHaveText("1 of 3");
});

// ------------------------------------------------------------------- search & palette

test("search_offers_recents_and_scopes_before_anything_is_typed", async ({ page }) => {
  await gotoApp(page, "/search");
  await expect(page.getByTestId("search-scopes")).toBeVisible();
  await expect(page.getByTestId("search-recent-meetings")).toContainText("Onboarding funnel review");

  await page.getByTestId("search-input").fill("activation");
  await page.getByTestId("search-input").press("Enter");
  await expect(page.getByTestId("search-group").first()).toBeVisible();
  await page.getByTestId("search-scope-action").click();
  await expect(page.getByTestId("search-group")).toHaveCount(1);
  await expect(page.getByTestId("search-group")).toHaveAttribute("data-kind", "action");

  // Remembered as a recent search.
  await page.getByTestId("search-input").fill("");
  await expect(page.getByTestId("search-recent")).toHaveText("activation");
  await page.getByTestId("search-recent").click();
  await expect(page.getByTestId("search-input")).toHaveValue("activation");
});

test("the_palette_searches_transcripts_in_groups_with_a_key_footer", async ({ page }) => {
  await gotoApp(page, "/");
  await page.keyboard.press("Control+k");
  await expect(page.getByTestId("palette-footer")).toContainText("move");
  await page.getByTestId("palette-input").fill("interstitial");
  const transcript = page.locator("[data-testid=palette-group][data-group='palette.transcript']");
  await expect(transcript).toBeVisible();
  await expect(transcript.getByTestId("palette-item").first()).toContainText("Dana Levi");
  await transcript.getByTestId("palette-item").first().click();
  await expect(page).toHaveURL(/\/m\/m-onboarding\?at=\d+/);
});

test("the_advertised_shortcuts_work", async ({ page }) => {
  await gotoApp(page, "/");
  await page.locator("body").press("/");
  await expect(page.getByTestId("search-page")).toBeVisible();
  // The search field takes focus on arrival; shortcuts are for when nothing is typed into.
  await page.locator("h1").click();
  await page.keyboard.press("g");
  await page.keyboard.press("a");
  await expect(page.getByTestId("actions-page")).toBeVisible();
  await page.keyboard.press("g");
  await page.keyboard.press("l");
  await expect(page.getByTestId("calendar-controls")).toBeVisible();
});

// ------------------------------------------------------------------- hebrew

test("the_new_surfaces_mirror_in_hebrew", async ({ page, request }) => {
  await request.put("/api/settings", {
    headers: { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` },
    data: { values: { "ui.language": "he" } },
  });
  await gotoApp(page, "/m/m-onboarding");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByTestId("meeting-actions-progress")).toHaveText("1 מתוך 3 בוצעו");
  await expect(page.getByTestId("copy-summary-bar")).toHaveText("העתקת הסיכום");
  // The rail sits at the inline end, which in Hebrew is the left.
  const rail = await page.getByTestId("meeting-rail").boundingBox();
  const column = await page.getByTestId("summary-html").boundingBox();
  expect((rail?.x ?? 0) < (column?.x ?? 0)).toBe(true);

  await gotoApp(page, "/actions");
  await expect(page.getByTestId("actions-tab-mine")).toContainText("שלי");
  await gotoApp(page, "/");
  await expect(page.getByTestId("start-recording")).toContainText("הקלטת פגישה");
});
