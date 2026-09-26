import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CalendarStatus } from "../api";
import { useI18n } from "../i18n";
import BusyButton from "./BusyButton";
import SettingRow, { SELECT_CLASS, SettingGroup } from "./SettingRow";
import { workingHours } from "../lib/calendar";

const PRIMARY = "rounded bg-accent px-2.5 py-1 text-sm text-on-accent";
const SECONDARY = "rounded border border-line px-2.5 py-1 text-sm";

/**
 * Connect a Google account (Calendar 1).
 *
 * The consent page opens in the user's own browser, in a new tab, and Google sends
 * the browser back to a listener the backend opened for this one connection. This
 * screen never sees a token: it asks for the status, and hears over the event
 * stream when the other tab has finished.
 */
export default function CalendarSettings() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [revokeByHand, setRevokeByHand] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ["calendar"],
    queryFn: api.calendarStatus,
    // The event stream is what normally updates this. The interval only covers a
    // stream that dropped while the user was away in Google's tab.
    refetchInterval: (query) => (query.state.data?.state === "connecting" ? 3000 : false),
  });
  const settle = (next: CalendarStatus) => queryClient.setQueryData(["calendar"], next);

  const connect = useMutation({
    mutationFn: async () => {
      /*
       * Opened before the request, inside the click, because a tab opened after an
       * await is a popup to the browser and gets blocked. It starts blank and is
       * pointed at Google once the backend has the URL ready.
       */
      const tab = window.open("about:blank", "_blank");
      if (tab) tab.opener = null;
      consentTab.current = tab;
      try {
        const next = await api.calendarConnect();
        if (tab) tab.location.href = next.auth_url;
        return next;
      } catch (error) {
        tab?.close();
        throw error;
      }
    },
    onSuccess: (next) => {
      setRevokeByHand(null);
      settle(next);
    },
  });
  const cancel = useMutation({ mutationFn: api.calendarCancel, onSuccess: settle });

  /*
   * Closing the Google tab cancels the connection.
   *
   * Nothing comes back when the consent tab is abandoned, so the backend sat in
   * `connecting` until CONNECT_TIMEOUT_S (300s) elapsed and this screen showed a
   * Cancel button for five minutes with nothing left to cancel. The tab is the
   * only thing that knows the user walked away, and we opened it, so we can watch
   * it: `closed` is readable on a window handle even after `opener` is cleared.
   *
   * Calling cancel after a *successful* sign-in is harmless — there is no pending
   * connection left to drop, and the endpoint answers with the real status either
   * way — so the race between "tab closed" and "token arrived" needs no lock.
   */
  const consentTab = useRef<Window | null>(null);
  useEffect(() => {
    if (status.data?.state !== "connecting") {
      consentTab.current = null;
      return undefined;
    }
    const tab = consentTab.current;
    if (!tab) return undefined;
    const timer = window.setInterval(() => {
      if (!tab.closed) return;
      window.clearInterval(timer);
      consentTab.current = null;
      cancel.mutate();
    }, 700);
    return () => window.clearInterval(timer);
    // `cancel` is a stable mutation object; re-running on it would restart the poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.data?.state]);
  const disconnect = useMutation({
    mutationFn: api.calendarDisconnect,
    onSuccess: (next) => {
      setRevokeByHand(next.revoke_by_hand);
      settle(next);
    },
  });

  const syncNow = useMutation({ mutationFn: api.calendarSyncNow, onSuccess: settle });
  const forget = useMutation({
    mutationFn: api.calendarForget,
    onSuccess: (next) => {
      settle(next);
      void queryClient.invalidateQueries({ queryKey: ["calendar-events"] });
    },
  });
  const data = status.data;
  const error = data?.error ?? (connect.error ? String(connect.error.message) : null);

  return (
    <SettingGroup>
      <SettingRow
        label={t("calendar.google")}
        description={
          <>
            <span className="block">{t("calendar.what")}</span>
            {data?.state === "connected" && data.account && (
              <span className="mt-1 block" data-testid="calendar-account">
                {t("calendar.connectedAs")} <bdi className="font-medium">{data.account}</bdi>
              </span>
            )}
            {data?.state === "connecting" && (
              <span className="mt-1 block" data-testid="calendar-waiting">
                {t("calendar.connecting")}{" "}
                {data.auth_url && (
                  <a href={data.auth_url} target="_blank" rel="noreferrer" className="underline">
                    {t("calendar.openAgain")}
                  </a>
                )}
              </span>
            )}
            {data?.state === "reconnect" && (
              <span className="mt-1 block text-warning" data-testid="calendar-reconnect">
                {t("calendar.reconnectHint")}
              </span>
            )}
            {data && !data.configured && (
              <span className="mt-1 block text-warning" data-testid="calendar-unconfigured">
                {t("calendar.notConfigured")}
              </span>
            )}
          </>
        }
        tone={
          <>
            {error && (
              <p data-testid="calendar-error" className="mt-1 text-xs text-danger">
                {error}
              </p>
            )}
            {revokeByHand && (
              <p data-testid="calendar-revoke-by-hand" className="mt-1 text-xs text-warning">
                {t("calendar.revokeByHand")}{" "}
                <a href={revokeByHand} target="_blank" rel="noreferrer" className="underline">
                  {revokeByHand}
                </a>
              </p>
            )}
          </>
        }
      >
        {data?.state === "connected" && (
          <>
            <span className="text-xs text-success" data-testid="calendar-connected">
              {t("calendar.connected")}
            </span>
            <BusyButton
              data-testid="calendar-disconnect"
              busy={disconnect.isPending}
              onClick={() => disconnect.mutate()}
              className={SECONDARY}
            >
              {t("calendar.disconnect")}
            </BusyButton>
          </>
        )}
        {data?.state === "connecting" && (
          <BusyButton
            data-testid="calendar-cancel"
            busy={cancel.isPending}
            onClick={() => cancel.mutate()}
            className={SECONDARY}
          >
            {t("calendar.cancel")}
          </BusyButton>
        )}
        {(data?.state === "disconnected" || data?.state === "reconnect") && (
          <BusyButton
            data-testid="calendar-connect"
            busy={connect.isPending}
            disabled={!data.configured}
            onClick={() => connect.mutate()}
            className={`${PRIMARY} disabled:opacity-40`}
          >
            {t(data.state === "reconnect" ? "calendar.reconnect" : "calendar.connect")}
          </BusyButton>
        )}
      </SettingRow>

      {data?.state === "connected" && (
        <SettingRow
          label={t("calendar.syncLabel")}
          description={
            <span data-testid="calendar-sync-status">
              {data.last_synced_at
                ? t("calendar.syncedAt")
                    .replace("{time}", new Date(data.last_synced_at).toLocaleString())
                    .replace("{count}", String(data.cached_events ?? 0))
                : t("calendar.notSyncedYet")}
              {data.sync_error && <span className="mt-1 block text-warning">{data.sync_error}</span>}
              <span className="mt-1 block">{t("calendar.whatIsKept")}</span>
            </span>
          }
        >
          <BusyButton
            data-testid="calendar-sync-now"
            busy={syncNow.isPending}
            onClick={() => syncNow.mutate()}
            className={SECONDARY}
          >
            {t("calendar.syncNow")}
          </BusyButton>
          <BusyButton
            data-testid="calendar-forget"
            busy={forget.isPending}
            onClick={() => forget.mutate()}
            className={SECONDARY}
          >
            {t("calendar.deleteCache")}
          </BusyButton>
        </SettingRow>
      )}

      <WorkingHoursRow />
    </SettingGroup>
  );
}

const HOUR_LABEL = (hour: number) => `${String(hour).padStart(2, "0")}:00`;

/**
 * The working day, which the day and week views open on and shade around. Not tied to
 * Google: it applies with no calendar connected, to the recordings alone. Each list only
 * offers hours that keep the day running forwards, so there is no invalid pair to save.
 */
function WorkingHoursRow() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const work = workingHours(settings.data?.config);
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: (next) => queryClient.setQueryData(["settings"], next),
  });
  const hours = Array.from({ length: 25 }, (_, hour) => hour);
  return (
    <SettingRow label={t("calendar.workingHours")} description={t("calendar.workingHoursHint")}>
      <label className="flex items-center gap-2 text-xs text-tertiary">
        {t("calendar.workFrom")}
        <select
          data-testid="work-start"
          value={work.start}
          onChange={(event) => save.mutate({ "calendar.work_start": Number(event.target.value) })}
          className={SELECT_CLASS}
        >
          {hours.filter((hour) => work.end > hour).map((hour) => (
            <option key={hour} value={hour}>
              {HOUR_LABEL(hour)}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-xs text-tertiary">
        {t("calendar.workTo")}
        <select
          data-testid="work-end"
          value={work.end}
          onChange={(event) => save.mutate({ "calendar.work_end": Number(event.target.value) })}
          className={SELECT_CLASS}
        >
          {hours.filter((hour) => hour > work.start).map((hour) => (
            <option key={hour} value={hour}>
              {HOUR_LABEL(hour)}
            </option>
          ))}
        </select>
      </label>
    </SettingRow>
  );
}
