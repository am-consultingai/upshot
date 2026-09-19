import { useCallback, useEffect, useMemo, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { I18nContext, applyLocale, catalogues, useI18n, type Locale, type MessageKey } from "./i18n";
import { applyTheme, isTheme, type Theme } from "./theme";
import Library from "./routes/Library";
import MeetingPage from "./routes/Meeting";
import SearchPage from "./routes/Search";
import Settings from "./routes/Settings";
import Detector from "./routes/Detector";
import Rail from "./components/Rail";
import RecordingBar from "./components/RecordingBar";
import DetectionNudge, { type Detection } from "./components/DetectionNudge";
import ConnectionBanner from "./components/ConnectionBanner";

/** A screen that is not one item from the library: it gets the whole width. */
function Full({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-4xl px-8 py-8">{children}</div>
    </div>
  );
}

/**
 * The detail side with nothing open yet. Worth a sentence rather than a blank
 * panel: an empty half-screen reads as something failing to load.
 */
function EmptyDetail() {
  const { t } = useI18n();
  return (
    <div
      data-testid="no-meeting-open"
      className="grid h-full place-items-center px-8 text-center text-sm text-tertiary"
    >
      {t("timeline.pickOne")}
    </div>
  );
}

export default function App() {
  const [locale, setLocale] = useState<Locale>("en");
  const [theme, setTheme] = useState<Theme>("light");
  const [detected, setDetected] = useState<Detection | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    applyLocale(locale);
  }, [locale]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  /*
   * Adopt what was saved. Both of these are stored server-side like every other
   * preference, so a reload used to drop straight back to English and light —
   * which for appearance is the more obvious wrong: choosing dark and having the
   * next launch flash white is worse than not offering the choice.
   */
  const saved = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const savedConfig = saved.data?.config as
    | { ui?: { language?: string; theme?: string } }
    | undefined;
  useEffect(() => {
    const language = savedConfig?.ui?.language;
    if (language === "en" || language === "he") setLocale(language);
  }, [savedConfig?.ui?.language]);
  useEffect(() => {
    const next = savedConfig?.ui?.theme;
    if (isTheme(next)) setTheme(next);
  }, [savedConfig?.ui?.theme]);

  useEffect(() => {
    const source = new EventSource("/api/events");
    /*
     * Whether the event stream is actually open, published on the document.
     *
     * Server-sent events have no replay: anything published between the page
     * loading and this connection opening is simply gone. For most events that
     * only costs a refresh, but the detection nudge exists *because* of one event,
     * so missing it means the nudge never appears for that meeting.
     *
     * Making the state observable is the honest fix. The browser specs wait on it
     * instead of racing it — a nudge test failed roughly one run in two — and it
     * gives the connection banner something better to read than a polled endpoint
     * that says nothing about the stream.
     */
    source.addEventListener("open", () => {
      document.documentElement.dataset.stream = "open";
    });
    source.addEventListener("error", () => {
      document.documentElement.dataset.stream = "closed";
    });
    const invalidate = () => {
      void queryClient.invalidateQueries();
    };
    source.addEventListener("job", invalidate);
    source.addEventListener("recorder", invalidate);
    source.addEventListener("meeting", invalidate);
    // Without this the Detector page only refreshed when something *else* happened, so a
    // detection took about a minute to appear — the detector reaches its verdict in ten.
    source.addEventListener("detector", (event) => {
      invalidate();
      const payload = JSON.parse((event as MessageEvent).data) as Detection & {
        state?: string;
      };
      // "shadow" is the verdict reached while only watching: a meeting we are not
      // recording. In automatic mode the recording has already started and the bar says so.
      if (payload.state === "shadow") setDetected(payload);
    });
    return () => {
      source.close();
      delete document.documentElement.dataset.stream;
    };
  }, [queryClient]);

  const t = useCallback((key: MessageKey) => catalogues[locale][key], [locale]);
  const value = useMemo(() => ({ locale, t, setLocale, theme, setTheme }), [locale, t, theme]);

  return (
    <I18nContext.Provider value={value}>
      <div className="flex h-screen overflow-hidden bg-canvas text-primary" data-testid="app">
        <Rail />
        <div className="flex min-w-0 flex-1 flex-col">
          <ConnectionBanner />
          <RecordingBar />
          <DetectionNudge detection={detected} onDismiss={() => setDetected(null)} />
          <main className="flex min-h-0 flex-1">
            <Routes>
              {/*
               * The library owns the list; what you open renders beside it. Search,
               * settings and the detector are whole screens rather than one item
               * from a collection, so they take the full width instead.
               */}
              <Route element={<Library />}>
                <Route path="/" element={<EmptyDetail />} />
                <Route path="/m/:id" element={<MeetingPage />} />
              </Route>
              <Route path="/search" element={<Full><SearchPage /></Full>} />
              <Route path="/settings" element={<Full><Settings /></Full>} />
              <Route path="/detector" element={<Full><Detector /></Full>} />
            </Routes>
          </main>
        </div>
      </div>
    </I18nContext.Provider>
  );
}
