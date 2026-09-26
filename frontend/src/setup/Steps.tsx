import { useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import BusyButton from "../components/BusyButton";
import MicMeter from "../components/MicMeter";
import { SELECT_CLASS } from "../components/SettingRow";
import { useSetupBackend, useSetupSnapshot } from "./backend";
import type { CaptureMode, Summarizer } from "./flow";
import { Note, PRIMARY, QUIET, StepFrame, fill } from "./ui";
import { CaptureScene, DoneMark, HeroFlow, SpeakerScene } from "./visuals";

type Nav = { onNext: () => void; onBack: () => void };

function Footer({ onNext, onBack }: Nav) {
  const { t } = useI18n();
  return (
    <>
      <button type="button" data-testid="setup-next" className={PRIMARY} onClick={onNext}>
        {t("firstRun.continue")}
      </button>
      <button type="button" data-testid="setup-back" className={QUIET} onClick={onBack}>
        {t("firstRun.back")}
      </button>
    </>
  );
}

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
 * The sound check. The microphone needs the user to speak; the computer's audio does
 * not: Upshot plays its own test sound the moment the step opens and listens for it on
 * the loopback, so that half is a self-test with nothing to press.
 */
export function AudioStep({ onNext, onBack }: Nav) {
  const { t } = useI18n();
  const backend = useSetupBackend();
  const { speakers } = useSetupSnapshot();

  // Once per visit; "Play again" is the way to repeat it.
  useEffect(() => {
    if (backend.snapshot().speakers.phase === "idle") backend.testSpeakers();
  }, [backend]);

  return (
    <StepFrame id="audio" title="firstRun.audio.title" footer={<Footer onNext={onNext} onBack={onBack} />}>
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
 * calendar on first run (CaptureChoice, now gone). Asked last, when the user has seen
 * what a recording turns into, with "detect and notify" chosen for them (D64).
 */
export function CaptureStep({
  mode,
  onChange,
  onNext,
  onBack,
}: Nav & { mode: CaptureMode; onChange: (mode: CaptureMode) => void }) {
  const { t } = useI18n();
  return (
    <StepFrame
      id="capture"
      title="firstRun.capture.title"
      lead={<p data-testid="capture-lead">{t("firstRun.capture.lead")}</p>}
      footer={<Footer onNext={onNext} onBack={onBack} />}
    >
      <div role="radiogroup" aria-labelledby="setup-title-capture" className="grid gap-3 sm:grid-cols-2">
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
              className={`flex flex-col gap-3 rounded-xl bg-raised p-4 text-start shadow-sm ring-inset hover:bg-a-100 ${
                chosen ? "ring-2 ring-accent" : "ring-1 ring-line-subtle"
              }`}
            >
              <div className="grid h-24 place-items-center rounded-lg bg-surface-2">
                <CaptureScene mode={option.mode} active={chosen} />
              </div>
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
              </span>
              <span className="text-xs text-tertiary">{t(option.why)}</span>
              {option.mode === "shadow" && (
                <span className="self-start rounded-full bg-accent-quiet px-2 py-0.5 text-2xs font-medium text-accent">
                  {t("firstRun.capture.recommended")}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </StepFrame>
  );
}

const CAPTURE_LABEL: Record<CaptureMode, MessageKey> = {
  shadow: "firstRun.capture.notify",
  on: "firstRun.capture.auto",
};

const SUMMARY_LABEL: Record<Summarizer["provider"], MessageKey> = {
  "claude-subscription": "firstRun.done.sum.claude-subscription",
  "codex-subscription": "firstRun.done.sum.codex-subscription",
  gemini: "firstRun.done.sum.gemini",
  anthropic: "firstRun.done.sum.anthropic",
  openai: "firstRun.done.sum.openai",
  none: "firstRun.done.sum.none",
};

type IconName = "calendar" | "spark" | "sound" | "record";

function Icon({ name, off }: { name: IconName; off: boolean }) {
  const stroke = off ? "var(--color-tertiary)" : "var(--color-accent)";
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-6 shrink-0" fill="none" stroke={stroke} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {name === "calendar" && (
        <>
          <rect x="3.5" y="5" width="17" height="15" rx="2.5" />
          <path d="M3.5 9.5h17M8 3v4M16 3v4" />
        </>
      )}
      {name === "spark" && <path d="M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z" />}
      {name === "record" && (
        <>
          <circle cx="12" cy="12" r="8.5" />
          <circle cx="12" cy="12" r="3.5" fill={stroke} />
        </>
      )}
      {name === "sound" && (
        <>
          <path d="M4 10h3l5-4v12l-5-4H4z" />
          <path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11" />
        </>
      )}
    </svg>
  );
}

/**
 * Setup 9: what was set up, what was skipped, and where to change either. A skipped
 * step gets a way to Settings, not a warning: skipping was allowed.
 */
export function DoneStep({
  summarizer,
  capture,
  finishing,
  onFinish,
  onBack,
}: {
  summarizer: Summarizer;
  capture: CaptureMode;
  finishing: boolean;
  onFinish: () => void;
  onBack: () => void;
}) {
  const { t } = useI18n();
  const { calendar, speakers } = useSetupSnapshot();
  const calendarOn = calendar.phase === "connected";
  const calendarValue = !calendar.available
    ? t("firstRun.done.calendarUnavailable")
    : calendarOn
      ? fill(t("firstRun.calendar.connectedAs"), { account: calendar.account ?? "" })
      : t("firstRun.done.calendarSkipped");

  return (
    <StepFrame
      id="done"
      title="firstRun.done.title"
      icon={<DoneMark className="size-9" />}
      footer={
        <>
          <BusyButton data-testid="setup-finish" busy={finishing} className={PRIMARY} onClick={onFinish}>
            {t("firstRun.done.start")}
          </BusyButton>
          <button type="button" data-testid="setup-back" className={QUIET} onClick={onBack}>
            {t("firstRun.back")}
          </button>
        </>
      }
    >
      <dl className="grid gap-2">
        <Row
          testId="done-calendar"
          icon="calendar"
          off={!calendarOn}
          label="firstRun.done.calendar"
          value={calendarValue}
          later={calendar.available && !calendarOn}
        />
        <Row
          testId="done-summaries"
          icon="spark"
          off={summarizer.provider === "none"}
          label="firstRun.done.summaries"
          value={t(SUMMARY_LABEL[summarizer.provider])}
          detail={
            summarizer.fallback
              ? t("firstRun.done.fallback")
              : summarizer.provider === "none"
                ? t("firstRun.done.noneNote")
                : undefined
          }
          later={summarizer.provider === "none"}
        />
        <Row
          testId="done-audio"
          icon="sound"
          off={speakers.phase === "silent"}
          label="firstRun.done.audio"
          value={speakers.phase === "silent" ? t("firstRun.done.audioSilent") : t("firstRun.done.audioOk")}
        />
        <Row testId="done-capture" icon="record" off={false} label="firstRun.done.capture" value={t(CAPTURE_LABEL[capture])} />
      </dl>
    </StepFrame>
  );
}

function Row({
  icon,
  off,
  label,
  value,
  detail,
  later = false,
  testId,
}: {
  icon: IconName;
  off: boolean;
  label: MessageKey;
  value: string;
  detail?: string;
  later?: boolean;
  testId: string;
}) {
  const { t } = useI18n();
  return (
    <div data-testid={testId} className="flex items-center gap-4 rounded-xl bg-raised px-4 py-3 shadow-sm">
      <Icon name={icon} off={off} />
      <div className="min-w-0 flex-1">
        <dt className="text-xs text-tertiary">{t(label)}</dt>
        <dd className="text-sm font-medium">{value}</dd>
        {detail && <dd className="text-xs text-tertiary">{detail}</dd>}
      </div>
      {later && (
        <Link to="/settings" className={`${QUIET} text-xs`}>
          {t("firstRun.done.later")}
        </Link>
      )}
    </div>
  );
}
