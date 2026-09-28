import { useEffect, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import BusyButton from "../components/BusyButton";
import MicMeter from "../components/MicMeter";
import { SELECT_CLASS } from "../components/SettingRow";
import { useSetupBackend, useSetupSnapshot } from "./backend";
import type { CaptureMode } from "./flow";
import { Note, PRIMARY, QUIET, StepFrame } from "./ui";
import { CaptureScene, HeroFlow, SpeakerScene } from "./visuals";

/** Record → transcript → summary, shown rather than said. */
export function WelcomeStep({ onNext }: { onNext: () => void }) {
  const { t } = useI18n();
  return (
    <StepFrame
      id="welcome"
      title="firstRun.welcome.title"
      lead={<p>{t("firstRun.welcome.optional")}</p>}
      footer={
        <button type="button" data-testid="setup-next" className={PRIMARY} onClick={onNext}>
          {t("firstRun.welcome.start")}
        </button>
      }
    >
      <HeroFlow />
    </StepFrame>
  );
}

function Panel({ title, children }: { title: MessageKey; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <section className="rounded-xl bg-raised px-5 py-4 shadow-sm">
      <h2 className="mb-3 text-sm font-medium">{t(title)}</h2>
      {children}
    </section>
  );
}

/**
 * The last step: the sound check, then how meetings get recorded, and Done.
 *
 * The microphone needs the user to speak; the computer's audio does not: Upshot plays its
 * own test sound the moment the step opens and listens for it on the loopback, so that
 * half is a self-test with nothing to press. The recording choice sits under it, with
 * "detect and notify" chosen (D64), and Done finishes setup: there is no summary screen
 * after it (product owner, 2026-09-28).
 */
export function AudioStep({
  mode,
  onChange,
  finishing,
  onFinish,
  onBack,
}: {
  mode: CaptureMode;
  onChange: (mode: CaptureMode) => void;
  finishing: boolean;
  onFinish: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const { speakers } = useSetupSnapshot();

  // Once per visit; "Play again" is the way to repeat it.
  useEffect(() => {
    if (backend.snapshot().speakers.phase === "idle") backend.testSpeakers();
  }, [backend]);

  return (
    <StepFrame
      id="audio"
      title="firstRun.audio.title"
      footer={
        <>
          <BusyButton data-testid="setup-finish" busy={finishing} className={PRIMARY} onClick={onFinish}>
            {t("firstRun.done.finish")}
          </BusyButton>
          <button type="button" data-testid="setup-back" className={QUIET} onClick={onBack}>
            {t("firstRun.back")}
          </button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Panel title="firstRun.audio.mic">
          <MicPicker />
        </Panel>
        <Panel title="firstRun.audio.speakers">
          <div data-testid="speaker-test" data-phase={speakers.phase} className="space-y-3">
            <SpeakerScene phase={speakers.phase} level={speakers.level} />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Note tone={speakers.phase === "heard" ? "good" : speakers.phase === "silent" ? "warn" : "neutral"}>
                {speakers.phase === "heard"
                  ? t("firstRun.audio.heard")
                  : speakers.phase === "silent"
                    ? t("firstRun.audio.silent")
                    : t("firstRun.audio.playing")}
              </Note>
              {(speakers.phase === "heard" || speakers.phase === "silent") && (
                <button type="button" data-testid="speaker-again" className={`${QUIET} text-xs`} onClick={() => backend.testSpeakers()}>
                  {t("firstRun.audio.again")}
                </button>
              )}
            </div>
          </div>
        </Panel>
      </div>
      <CaptureChoice mode={mode} onChange={onChange} />
    </StepFrame>
  );
}

/** The microphone list and its live meter, writing the same setting Settings does. */
function MicPicker() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const audio = useQuery({ queryKey: ["audio-devices"], queryFn: api.audioDevices });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });
  const config = (settings.data?.config ?? {}) as { audio?: { input_device?: number | null } };
  const selected = config.audio?.input_device ?? null;
  return (
    <div className="space-y-3">
      <select
        data-testid="mic-device"
        aria-label={t("firstRun.audio.mic")}
        value={selected === null ? "" : String(selected)}
        onChange={(event) =>
          save.mutate({ "audio.input_device": event.target.value === "" ? null : Number(event.target.value) })
        }
        className={`${SELECT_CLASS} w-full`}
      >
        <option value="">{t("settings.microphoneDefault")}</option>
        {(audio.data?.devices ?? []).map((device) => (
          <option key={device.index} value={device.index}>
            {device.name}
          </option>
        ))}
      </select>
      <MicMeter device={selected} />
    </div>
  );
}

const CAPTURE_OPTIONS: { mode: CaptureMode; title: MessageKey; why: MessageKey }[] = [
  { mode: "shadow", title: "firstRun.capture.notify", why: "firstRun.capture.notifyWhy" },
  { mode: "on", title: "firstRun.capture.auto", why: "firstRun.capture.autoWhy" },
];

/**
 * How meetings get recorded: the question the library used to ask in a banner over the
 * calendar on first run (CaptureChoice, now gone), with "detect and notify" chosen for
 * the user (D64).
 */
function CaptureChoice({ mode, onChange }: { mode: CaptureMode; onChange: (mode: CaptureMode) => void }) {
  const { t } = useI18n();
  return (
    <section data-testid="capture-choice" className="mt-6">
      <h2 id="setup-capture-title" className="text-base font-medium">
        {t("firstRun.capture.title")}
      </h2>
      <p data-testid="capture-lead" className="mb-3 text-sm text-secondary">
        {t("firstRun.capture.lead")}
      </p>
      <div role="radiogroup" aria-labelledby="setup-capture-title" className="grid gap-3 sm:grid-cols-2">
        {CAPTURE_OPTIONS.map((option) => {
          const chosen = option.mode === mode;
          return (
            <button
              key={option.mode}
              type="button"
              role="radio"
              aria-checked={chosen}
              data-testid={`capture-${option.mode}`}
              onClick={() => onChange(option.mode)}
              className={`flex items-center gap-3 rounded-xl bg-raised p-3 text-start shadow-sm ring-inset hover:bg-a-100 ${
                chosen ? "ring-2 ring-accent" : "ring-1 ring-line-subtle"
              }`}
            >
              {/* The scene at 4/5 of its 160x80, whole: it scales by its viewBox. */}
              <div className="grid h-16 w-32 shrink-0 place-items-center overflow-hidden rounded-lg bg-surface-2 [&_svg]:h-16 [&_svg]:w-32">
                <CaptureScene mode={option.mode} active={chosen} />
              </div>
              <span className="flex min-w-0 flex-col gap-1">
                <span className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className={`grid size-4 shrink-0 place-items-center rounded-full ${
                      chosen ? "bg-accent" : "ring-1 ring-line-strong"
                    }`}
                  >
                    {chosen && <span className="size-1.5 rounded-full bg-on-accent" />}
                  </span>
                  <span className="text-sm font-medium">{t(option.title)}</span>
                  {option.mode === "shadow" && (
                    <span className="rounded-full bg-accent-quiet px-2 py-0.5 text-2xs font-medium text-accent">
                      {t("firstRun.capture.recommended")}
                    </span>
                  )}
                </span>
                <span className="text-xs text-tertiary">{t(option.why)}</span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
