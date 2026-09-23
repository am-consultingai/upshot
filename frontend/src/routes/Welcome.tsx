import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import AudioDeviceSettings from "../components/AudioDeviceSettings";
import BusyButton from "../components/BusyButton";
import { PinnedContext, SettingGroup } from "../components/SettingRow";
import {
  ComputeDeviceRow,
  MeetingLanguageRow,
  SpeechModelRow,
  useModelStatus,
  useSpeechSave,
} from "../components/SpeechSettings";
import {
  initialMeetingLanguage,
  meetingLanguageValues,
  type AsrLanguageConfig,
  type MeetingLanguage,
} from "../lib/speech";

/** One numbered step: the heading says what it is, the rows below do it. */
function Step({ n, title, hint, children }: { n: number; title: MessageKey; hint?: MessageKey; children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <section data-testid={`welcome-step-${n}`} className="mb-8">
      <h2 className="mb-1.5 flex items-baseline gap-2 px-1 text-sm font-medium">
        <span className="text-tertiary tabular-nums">{n}</span>
        <span>{t(title)}</span>
      </h2>
      {hint && <p className="mb-2 max-w-prose px-1 text-xs leading-relaxed text-tertiary">{t(hint)}</p>}
      <SettingGroup>{children}</SettingGroup>
    </section>
  );
}

/**
 * First-run setup (ClickUp z8tj1had06).
 *
 * A stranger installing Upshot on Windows had no speech model on disk and no way to learn
 * that until their first meeting sat "transcribing" for minutes; and the default language
 * handling pinned an English meeting as Hebrew. This screen asks the four things that
 * decide whether the first meeting works, in the order they depend on each other: the
 * language picks the model, the model is downloaded, the devices are tested, and the
 * device plan says how long a transcript will take.
 *
 * There is no AI provider step and no calendar step, deliberately: both are optional to
 * a working transcript, both live in Settings with their own "!" until set up, and a
 * first-run screen that asks for an API key is one people close.
 *
 * Every control applies the moment it changes, like Settings. "Done" marks setup finished
 * (`setup.done`) and saves the language shown, so the preselected Hebrew is a choice
 * rather than an accident of the default; "Skip" marks it finished and changes nothing.
 */
export default function Welcome() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const model = useModelStatus();
  const saveSpeech = useSpeechSave();
  const config = settings.data?.config as
    | { asr?: AsrLanguageConfig; setup?: { done?: boolean } }
    | undefined;
  const [chosen, setChosen] = useState<MeetingLanguage | null>(null);
  const language = chosen ?? initialMeetingLanguage(config?.asr, config?.setup?.done === true);

  const finish = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api.putSettings({ ...values, "setup.done": true }),
    onSuccess: (next) => {
      // Set rather than invalidated: the shell reads this to decide whether to send the
      // next screen back here, and a refetch in flight would briefly say "not done".
      queryClient.setQueryData(["settings"], next);
      navigate("/", { replace: true });
    },
  });

  return (
    <PinnedContext.Provider value={settings.data?.pinned ?? {}}>
      <section data-testid="welcome-page">
        <h1 className="display mb-2 text-2xl">{t("welcome.title")}</h1>
        <p className="mb-8 max-w-prose text-sm text-secondary">{t("welcome.intro")}</p>

        <Step n={1} title="speech.language">
          <MeetingLanguageRow
            selected={language}
            onChoose={(choice) => {
              setChosen(choice);
              saveSpeech.mutate(meetingLanguageValues(choice));
            }}
          />
        </Step>

        <Step n={2} title="speech.model">
          <SpeechModelRow />
        </Step>

        <Step n={3} title="welcome.audio" hint="welcome.audioHint">
          <AudioDeviceSettings />
        </Step>

        <Step n={4} title="welcome.device">
          <ComputeDeviceRow />
        </Step>

        {model.data && model.data.state !== "ready" && (
          <p data-testid="welcome-model-later" className="mb-4 max-w-prose text-xs text-tertiary">
            {t("welcome.modelLater")}
          </p>
        )}
        <div className="flex items-center gap-3">
          <BusyButton
            data-testid="welcome-done"
            busy={finish.isPending}
            onClick={() => finish.mutate(meetingLanguageValues(language))}
            className="rounded bg-accent px-3 py-1.5 text-sm text-on-accent"
          >
            {t("welcome.done")}
          </BusyButton>
          <button
            type="button"
            data-testid="welcome-skip"
            disabled={finish.isPending}
            onClick={() => finish.mutate({})}
            className="rounded px-3 py-1.5 text-sm text-secondary hover:bg-a-200 active:bg-a-300"
          >
            {t("welcome.skip")}
          </button>
        </div>
        {finish.error && (
          <p data-testid="welcome-error" className="mt-2 text-xs text-danger">
            {String(finish.error.message)}
          </p>
        )}
      </section>
    </PinnedContext.Provider>
  );
}
