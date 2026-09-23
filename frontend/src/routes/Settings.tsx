import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type Locale } from "../i18n";
import { THEMES, type Theme } from "../theme";
import ProviderSettings from "../components/ProviderSettings";
import PromptSettings from "../components/PromptSettings";
import CalendarSettings from "../components/CalendarSettings";
import AudioDeviceSettings from "../components/AudioDeviceSettings";
import SpeechSettings from "../components/SpeechSettings";
import SettingRow, { PinnedContext, SELECT_CLASS, SettingGroup } from "../components/SettingRow";
import SettingsNav, { useSection, type SettingsSection } from "../components/SettingsNav";
import { useSetupWarnings } from "../lib/setup";

/** Language endonyms are data, not copy: they are never translated. */
const LANGUAGE_NAMES: Record<string, string> = { en: "English", he: "עברית", auto: "auto" };

interface ConfigShape {
  data_root?: string | null;
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
 * Sections, in the order someone meets them: what it records, how it looks,
 * where it puts things, what it connects to, then the two long ones.
 */
const SECTIONS: SettingsSection[] = [
  { id: "audio", label: "settings.groupAudio" },
  // What first-run setup asks, kept reachable after it: the meeting language, the
  // speech model and where it runs.
  { id: "speech", label: "settings.groupSpeech" },
  { id: "appearance", label: "settings.groupAppearance" },
  { id: "storage", label: "settings.groupStorage" },
  { id: "calendar", label: "settings.groupCalendar" },
  { id: "summaries", label: "settings.groupSummaries", hint: "help.aiAgents" },
  { id: "prompt", label: "settings.prompt" },
];

export default function Settings() {
  const section = useSection(SECTIONS);
  const setup = useSetupWarnings();
  const { t, locale, setLocale, theme, setTheme, tooltips, setTooltips } = useI18n();
  const queryClient = useQueryClient();
  const [dataRoot, setDataRoot] = useState("");
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const config = (settings.data?.config ?? {}) as ConfigShape;

  useEffect(() => {
    if (config.data_root !== undefined && config.data_root !== null) setDataRoot(config.data_root);
  }, [config.data_root]);

  const warnings = settings.data?.warnings ?? [];
  // Keys the environment is holding down. Handed to every row, so a control a launcher
  // has pinned admits it rather than saving, reading back, and reverting on the next
  // start with nothing on screen to explain it.
  const pinned = settings.data?.pinned ?? {};
  const localWarning = /onedrive|dropbox|google drive/i.test(dataRoot);

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
       * own new state is the acknowledgement. The one exception is the data folder,
       * which is a free-text path and cannot be applied on every keystroke.
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

      {section === "storage" && (
      <SettingGroup>
        <SettingRow
          label={t("settings.dataRoot")}
          configKey="data_root"
          htmlFor="data-root"
          description={t("settings.dataRootNote")}
          tone={
            (localWarning || warnings.length > 0) && (
              <p data-testid="sync-warning" className="mt-1 text-xs text-warning">
                {t("settings.syncWarning")}
              </p>
            )
          }
        >
          {/*
           * The one setting that is not instant: a path is only meaningful once it
           * is finished being typed, so this commits on blur and on Enter rather
           * than on every keystroke.
           */}
          <input
            id="data-root"
            data-testid="data-root"
            value={dataRoot}
            onChange={(event) => setDataRoot(event.target.value)}
            onBlur={() => dataRoot && save.mutate({ data_root: dataRoot })}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            spellCheck={false}
            className="w-[22rem] max-w-full rounded-sm border border-line bg-canvas px-2 py-1.5 font-mono text-xs"
          />
          {save.isSuccess && (
            <span data-testid="settings-saved" className="text-xs text-success">
              {t("settings.saved")}
            </span>
          )}
        </SettingRow>
      </SettingGroup>
      )}

      {section === "speech" && <SpeechSettings />}
      {section === "calendar" && <CalendarSettings />}
      {section === "summaries" && <ProviderSettings />}
      {section === "prompt" && <PromptSettings />}
      </div>
    </section>
    </PinnedContext.Provider>
  );
}
