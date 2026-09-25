import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type Locale } from "../i18n";
import { THEMES, type Theme } from "../theme";
import ProviderSettings from "../components/ProviderSettings";
import PromptSettings from "../components/PromptSettings";
import CalendarSettings from "../components/CalendarSettings";
import AudioDeviceSettings from "../components/AudioDeviceSettings";
import SettingRow, { PinnedContext, SELECT_CLASS, SettingGroup } from "../components/SettingRow";
import SettingsNav, { useSection, type SettingsSection } from "../components/SettingsNav";
import { useSetupWarnings } from "../lib/setup";

/** Language endonyms are data, not copy: they are never translated. */
const LANGUAGE_NAMES: Record<string, string> = { en: "English", he: "עברית", auto: "auto" };

interface ConfigShape {
  ui?: { language?: string };
  summary?: { language?: string };
  detection?: { mode?: string };
}

/** What the three detection modes mean, said in terms of what happens to you. */
const DETECTION_MODES = [
  { value: "shadow", label: "settings.detectionWatch" },
  { value: "on", label: "settings.detectionAuto" },
  { value: "off", label: "settings.detectionOff" },
] as const;

/*
 * Sections, in the order someone meets them: what it records, how it looks, what it
 * connects to, then the two long ones. There is no Transcription or Storage section:
 * which model runs, where it runs and where recordings are kept are the app's business,
 * not the user's (first-run setup still downloads the model and says where it runs).
 */
const SECTIONS: SettingsSection[] = [
  { id: "audio", label: "settings.groupAudio" },
  { id: "appearance", label: "settings.groupAppearance" },
  { id: "calendar", label: "settings.groupCalendar" },
  { id: "summaries", label: "settings.groupSummaries", hint: "help.aiAgents" },
  { id: "prompt", label: "settings.prompt" },
];

export default function Settings() {
  const section = useSection(SECTIONS);
  const setup = useSetupWarnings();
  const { t, locale, setLocale, theme, setTheme, tooltips, setTooltips } = useI18n();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const config = (settings.data?.config ?? {}) as ConfigShape;

  // Keys the environment is holding down. Handed to every row, so a control a launcher
  // has pinned admits it rather than saving, reading back, and reverting on the next
  // start with nothing on screen to explain it.
  const pinned = settings.data?.pinned ?? {};

  return (
    <PinnedContext.Provider value={pinned}>
    <section data-testid="settings-page" className="flex gap-10">
      <SettingsNav
        sections={SECTIONS}
        warnings={{
          ...(setup.calendar ? { calendar: t("setup.calendarMissing") } : {}),
          ...(setup.summaries ? { summaries: t("setup.summariesMissing") } : {}),
        }}
      />

      <div className="min-w-0 flex-1">
      <h1 className="display mb-6 text-2xl">{t(SECTIONS.find((s) => s.id === section)!.label)}</h1>

      {/*
       * Everything here applies the moment it changes. There is no Save button and
       * no confirmation toast, which is the current convention and the documented
       * Windows rule: "when a user changes a setting, the app should immediately
       * reflect the change — don't require a confirmation button." The control's
       * own new state is the acknowledgement.
       */}

      {section === "audio" && (
      <SettingGroup>
        <AudioDeviceSettings />

        <SettingRow
          label={t("settings.detection")}
          htmlFor="detection-mode"
          configKey="detection.mode"
          description={<span data-testid="detection-hint">{t("settings.detectionHint")}</span>}
        >
          <select
            id="detection-mode"
            data-testid="detection-mode"
            value={config.detection?.mode ?? "shadow"}
            onChange={(event) => save.mutate({ "detection.mode": event.target.value })}
            className={SELECT_CLASS}
          >
            {DETECTION_MODES.map((option) => (
              <option key={option.value} value={option.value}>
                {t(option.label)}
              </option>
            ))}
          </select>
        </SettingRow>
      </SettingGroup>
      )}

      {section === "appearance" && (
      <SettingGroup>
        <SettingRow label={t("settings.theme")} htmlFor="ui-theme" configKey="ui.theme">
          <select
            id="ui-theme"
            data-testid="ui-theme"
            value={theme}
            onChange={(event) => {
              const next = event.target.value as Theme;
              // Applied before it is saved: appearance should answer the click, not
              // the round trip.
              setTheme(next);
              save.mutate({ "ui.theme": next });
            }}
            className={SELECT_CLASS}
          >
            {THEMES.map((option) => (
              <option key={option} value={option}>
                {t(
                  option === "light"
                    ? "settings.themeLight"
                    : option === "dark"
                      ? "settings.themeDark"
                      : "settings.themeSystem",
                )}
              </option>
            ))}
          </select>
        </SettingRow>

        <SettingRow
          label={t("settings.language")}
          htmlFor="ui-language"
          configKey="ui.language"
        >
          <select
            id="ui-language"
            data-testid="ui-language"
            value={locale}
            onChange={(event) => {
              const next = event.target.value as Locale;
              setLocale(next);
              save.mutate({ "ui.language": next });
            }}
            className={SELECT_CLASS}
          >
            {["en", "he"].map((code) => (
              <option key={code} value={code}>
                {LANGUAGE_NAMES[code]}
              </option>
            ))}
          </select>
        </SettingRow>

        <SettingRow
          label={t("settings.summaryLanguage")}
          htmlFor="summary-language"
          configKey="summary.language"
        >
          <select
            id="summary-language"
            data-testid="summary-language"
            value={config.summary?.language ?? "en"}
            onChange={(event) => save.mutate({ "summary.language": event.target.value })}
            className={SELECT_CLASS}
          >
            {["en", "he", "auto"].map((code) => (
              <option key={code} value={code}>
                {LANGUAGE_NAMES[code]}
              </option>
            ))}
          </select>
        </SettingRow>

        {/*
         * Off by default — the tooltips are how a new user learns what "Up next" or
         * "Ask this meeting" is for — and one click away for someone who has.
         */}
        <SettingRow
          label={t("settings.tooltipsOff")}
          htmlFor="ui-tooltips-off"
          configKey="ui.tooltips_off"
          description={t("settings.tooltipsOffHint")}
        >
          <input
            id="ui-tooltips-off"
            data-testid="ui-tooltips-off"
            type="checkbox"
            checked={!tooltips}
            onChange={(event) => {
              setTooltips(!event.target.checked);
              save.mutate({ "ui.tooltips_off": event.target.checked });
            }}
            className="size-4 accent-[var(--accent)]"
          />
        </SettingRow>
      </SettingGroup>
      )}

      {section === "calendar" && <CalendarSettings />}
      {section === "summaries" && <ProviderSettings />}
      {section === "prompt" && <PromptSettings />}
      </div>
    </section>
    </PinnedContext.Provider>
  );
}
