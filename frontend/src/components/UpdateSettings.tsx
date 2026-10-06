import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type UpdateState } from "../api";
import { useI18n } from "../i18n";
import BusyButton from "./BusyButton";
import SettingRow, { SettingGroup } from "./SettingRow";
import { openFeedback } from "./FeedbackDialog";

const PRIMARY = "rounded bg-accent px-2.5 py-1 text-sm text-on-accent";
const SECONDARY = "rounded border border-line px-2.5 py-1 text-sm";

/** Phases that change by themselves: watched closely until they settle. */
const MOVING = new Set<UpdateState["phase"]>(["checking", "downloading"]);

/**
 * About and updates (D87): which build this is, what the next one is, and the two
 * choices a person has about it. The app finds, downloads and installs updates by
 * itself, like Windows Update, so this screen mostly says what is happening; "Check
 * now" and "Restart to update" are there for someone who does not want to wait.
 */
export default function UpdateSettings() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const updates = useQuery({
    queryKey: ["updates"],
    queryFn: api.updates,
    refetchInterval: (query) => (query.state.data && MOVING.has(query.state.data.phase) ? 2000 : false),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["updates"] });
  const check = useMutation({ mutationFn: api.checkUpdates, onSuccess: refresh });
  const install = useMutation({ mutationFn: api.installUpdate });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void refresh();
    },
  });
  // The switches answer the click, not the round trip, as the other settings do.
  const [auto, setAuto] = useState<boolean | null>(null);
  const [beta, setBeta] = useState<boolean | null>(null);

  const build = status.data?.build;
  const state = updates.data;
  const offer = state?.available ?? null;
  const notes = offer ? (offer.notes[locale] ?? offer.notes.en ?? "") : "";
  const canInstall = Boolean(state?.ready && state.install?.can_install);
  const lastFailed = state?.install?.last?.result === "failed" ? state.install.last : null;

  return (
    <SettingGroup>
      <SettingRow
        label={t("updates.version")}
        description={
          <span data-testid="about-version" dir="ltr">
            {build
              ? build.commit
                ? t("updates.versionValue")
                    .replace("{version}", build.version)
                    .replace("{commit}", build.commit.slice(0, 7))
                : build.version
              : "…"}
          </span>
        }
      >
        <span />
      </SettingRow>

      <SettingRow label={t("feedback.title")} description={t("feedback.settingsHint")}>
        <button type="button" data-testid="open-feedback" className={SECONDARY} onClick={openFeedback}>
          {t("feedback.open")}
        </button>
      </SettingRow>

      <SettingRow
        label={t("updates.status")}
        description={state && <StatusText state={state} />}
        tone={
          state && (
            <>
              {state.phase === "downloading" && state.progress && (
                <div className="mt-2 max-w-prose">
                  <div
                    data-testid="update-progress"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={percent(state)}
                    aria-label={t("updates.status")}
                    className="h-1.5 overflow-hidden rounded-full bg-surface-3"
                  >
                    <div
                      className="h-full rounded-full bg-accent transition-[width]"
                      style={{ width: `${percent(state)}%` }}
                    />
                  </div>
                </div>
              )}
              {lastFailed && (
                <p data-testid="update-install-failed" className="mt-1 text-xs text-danger">
                  {t("updates.installFailed").replace("{version}", lastFailed.to)}
                </p>
              )}
              {notes && (
                <p data-testid="update-notes" className="mt-2 max-w-prose text-xs text-secondary">
                  <span className="font-medium text-primary">{t("updates.whatsNew")}: </span>
                  {notes}
                  {offer?.notes_url && (
                    <>
                      {" "}
                      <a href={offer.notes_url} target="_blank" rel="noreferrer" className="underline">
                        ↗
                      </a>
                    </>
                  )}
                </p>
              )}
              {state.last_checked_at && (
                <p data-testid="update-last-checked" className="mt-1 text-xs text-tertiary">
                  {t("updates.lastChecked").replace(
                    "{time}",
                    new Date(state.last_checked_at).toLocaleString(locale),
                  )}
                </p>
              )}
            </>
          )
        }
      >
        <div className="flex gap-2">
          {canInstall && (
            <BusyButton
              data-testid="update-install"
              busy={install.isPending || install.isSuccess}
              onClick={() => install.mutate()}
              className={PRIMARY}
            >
              {t(install.isPending || install.isSuccess ? "updates.installing" : "updates.installNow")}
            </BusyButton>
          )}
          <BusyButton
            data-testid="update-check"
            busy={check.isPending || state?.phase === "checking"}
            onClick={() => check.mutate()}
            className={SECONDARY}
          >
            {t("updates.checkNow")}
          </BusyButton>
        </div>
      </SettingRow>

      <SettingRow
        label={t("updates.auto")}
        htmlFor="updates-auto"
        configKey="updates.auto_install"
        description={t("updates.autoHint")}
      >
        <input
          id="updates-auto"
          data-testid="updates-auto"
          type="checkbox"
          role="switch"
          checked={auto ?? state?.auto_install ?? true}
          onChange={(event) => {
            setAuto(event.target.checked);
            save.mutate({ "updates.auto_install": event.target.checked });
          }}
          className="size-4 accent-[var(--accent)]"
        />
      </SettingRow>

      <SettingRow
        label={t("updates.beta")}
        htmlFor="updates-beta"
        configKey="updates.channel"
        description={t("updates.betaHint")}
      >
        <input
          id="updates-beta"
          data-testid="updates-beta"
          type="checkbox"
          role="switch"
          checked={beta ?? state?.channel === "beta"}
          onChange={(event) => {
            setBeta(event.target.checked);
            save.mutate({ "updates.channel": event.target.checked ? "beta" : "stable" });
          }}
          className="size-4 accent-[var(--accent)]"
        />
      </SettingRow>
    </SettingGroup>
  );
}

function percent(state: UpdateState): number {
  const progress = state.progress;
  if (!progress || !progress.total) return 0;
  return Math.min(100, Math.round((progress.bytes / progress.total) * 100));
}

/** One line on what the updater is doing, in the words a person would use. */
function StatusText({ state }: { state: UpdateState }) {
  const { t } = useI18n();
  const version = state.available?.version ?? "";
  const lines: string[] = [];
  if (!state.enabled) lines.push(t("updates.fromSource"));
  switch (state.phase) {
    case "checking":
      lines.push(t("updates.checking"));
      break;
    case "downloading":
      lines.push(
        t("updates.downloading").replace("{version}", version).replace("{percent}", String(percent(state))),
      );
      break;
    case "waiting":
      lines.push(t("updates.waiting").replace("{version}", version));
      break;
    case "ready":
      lines.push(t("updates.ready").replace("{version}", version));
      if (state.available?.mandatory) lines.push(t("updates.mandatory"));
      else if (state.auto_install && state.install?.can_install) lines.push(t("updates.readyAuto"));
      if (state.install?.waiting_for) {
        lines.push(
          t("updates.waitingFor").replace(
            "{reason}",
            t(`updates.reason.${state.install.waiting_for}` as const),
          ),
        );
      }
      break;
    case "failed":
      lines.push(t("updates.failed"));
      break;
    default:
      lines.push(version ? t("updates.available").replace("{version}", version) : t("updates.upToDate"));
  }
  return (
    <span data-testid="update-status" data-phase={state.phase}>
      {lines.join(" ")}
    </span>
  );
}
