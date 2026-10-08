import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type Locale } from "../i18n";
import { THEMES, type Theme } from "../theme";
import ProviderSettings from "../components/ProviderSettings";
import PromptSettings from "../components/PromptSettings";
import CalendarSettings from "../components/CalendarSettings";
import AudioDeviceSettings from "../components/AudioDeviceSettings";
import TranscriptionServiceSettings from "../components/TranscriptionServiceSettings";
import UpdateSettings from "../components/UpdateSettings";
import PrivacySettings from "../components/PrivacySettings";
import StorageSettings from "../components/StorageSettings";
import SettingRow, { PinnedContext, SELECT_CLASS, SettingGroup } from "../components/SettingRow";
import SettingsNav, { useSection, type SettingsSection } from "../components/SettingsNav";
import Tooltip from "../components/Tooltip";
import { useSetupWarnings } from "../lib/setup";
import { DETECTION_MODES } from "../lib/detection";

/** Language endonyms are data, not copy: they are never translated. */
const LANGUAGE_NAMES: Record<string, string> = { en: "English", he: "עברית", auto: "auto" };

interface ConfigShape {
  ui?: { language?: string };
  summary?: { language?: string };
  detection?: { mode?: string };
  asr?: { cpu_fast?: boolean };
}

/*
 * Sections, in the order someone meets them: what it records, how it looks, what it
 * connects to, then the two long ones. Which model runs and where it runs are the
 * app's business, not the user's (first-run setup still downloads the model and says
 * where it runs). Where recordings are kept is shown, not chosen: Storage says where
 * and how much, which is how "local only" can be checked.
 *
 * Each carries one line of purpose under its heading, said in terms of what it is for
 * rather than what is in it — the name says the second.
 */
const SECTIONS: SettingsSection[] = [
  { id: "audio", label: "settings.groupAudio", purpose: "settings.purposeAudio" },
  { id: "appearance", label: "settings.groupAppearance", purpose: "settings.purposeAppearance" },
  { id: "calendar", label: "settings.groupCalendar", purpose: "settings.purposeCalendar" },
  { id: "summaries", label: "settings.groupSummaries", hint: "help.aiAgents", purpose: "settings.purposeSummaries" },
  { id: "prompt", label: "settings.prompt", purpose: "settings.purposePrompt" },
  { id: "transcription", label: "settings.groupTranscription", purpose: "settings.purposeTranscription" },
  { id: "storage", label: "settings.groupStorage", purpose: "settings.purposeStorage" },
  // Crash reports: the answer given in setup, and what was last sent (D87).
  { id: "privacy", label: "settings.groupPrivacy", purpose: "settings.purposePrivacy" },
  // Last: what version this is, and the updates that arrive by themselves (D87).
  { id: "about", label: "settings.groupAbout", purpose: "settings.purposeAbout" },
];

export default function Settings() {
  const section = useSection(SECTIONS);
  const current = SECTIONS.find((s) => s.id === section)!;
  const setup = useSetupWarnings();
  const { t, locale, setLocale, theme, setTheme, tooltips, setTooltips } = useI18n();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const config = (settings.data?.config ?? {}) as ConfigShape;
  // What the switch shows while its save is on the way: it flips on the click, as the
  // tooltips switch does, rather than after the round trip.
  const [cpuFast, setCpuFast] = useState<boolean | null>(null);

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
      <header className="mb-6">
        <h1 className="display text-2xl">{t(current.label)}</h1>
        {current.purpose && (
          <p data-testid="settings-purpose" className="mt-1 max-w-prose text-sm text-secondary">
            {t(current.purpose)}
          </p>
        )}
      </header>

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

        {/*
         * "CPU acceleration": greedy decoding on the CPU (asr.cpu_fast). The GPU always
         * uses full quality, so the switch only matters on a machine without one.
         */}
        <SettingRow
          label={t("settings.cpuFast")}
          htmlFor="cpu-fast"
          configKey="asr.cpu_fast"
          description={<span data-testid="cpu-fast-hint">{t("settings.cpuFastHint")}</span>}
        >
          <Tooltip label={t("settings.cpuFast")} hint={t("settings.cpuFastHint")} side="bottom">
            <input
              id="cpu-fast"
              data-testid="cpu-fast"
              type="checkbox"
              role="switch"
              aria-description={t("settings.cpuFastHint")}
              checked={cpuFast ?? config.asr?.cpu_fast ?? false}
              onChange={(event) => {
                setCpuFast(event.target.checked);
                save.mutate({ "asr.cpu_fast": event.target.checked });
              }}
              className="size-4 accent-[var(--accent)]"
            />
          </Tooltip>
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
      {section === "transcription" && <TranscriptionServiceSettings />}
      {section === "storage" && <StorageSettings />}
      {section === "privacy" && <PrivacySettings />}
      {section === "about" && <UpdateSettings />}
      </div>
    </section>
    </PinnedContext.Provider>
  );
}
