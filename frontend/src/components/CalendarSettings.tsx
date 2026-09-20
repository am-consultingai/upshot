import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CalendarStatus } from "../api";
import { useI18n } from "../i18n";
import BusyButton from "./BusyButton";
import SettingRow, { SettingGroup } from "./SettingRow";

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
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const prompt = (
    (settings.data?.config ?? {}) as { calendar?: { prompt_invite?: boolean } }
  ).calendar;
  const saveSetting = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
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

      {/*
       * The invitation is context for the summary, and it is on by default: a model that
       * knows what the meeting was for writes about that meeting. Addresses are not part
       * of it — an attendee is a name long before this switch is read.
       */}
      <SettingRow
        label={t("calendar.promptInvite")}
        htmlFor="calendar-prompt-invite"
        description={t("calendar.promptInviteHint")}
      >
        <input
          id="calendar-prompt-invite"
          data-testid="calendar-prompt-invite"
          type="checkbox"
          checked={prompt?.prompt_invite ?? true}
          onChange={(change) =>
            saveSetting.mutate({ "calendar.prompt_invite": change.target.checked })
          }
          className="size-4"
        />
      </SettingRow>
    </SettingGroup>
  );
}
