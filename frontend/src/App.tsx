import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { I18nContext, applyLocale, catalogues, type Locale, type MessageKey } from "./i18n";
import { applyTheme, isTheme, type Theme } from "./theme";
import Timeline from "./routes/Timeline";
import MeetingPage from "./routes/Meeting";
import SearchPage from "./routes/Search";
import Settings from "./routes/Settings";
import Detector from "./routes/Detector";
import RecordingBar from "./components/RecordingBar";
import DetectionNudge, { type Detection } from "./components/DetectionNudge";
import ConnectionBanner from "./components/ConnectionBanner";

const NAV: { to: string; key: MessageKey; testid: string }[] = [
  { to: "/", key: "nav.timeline", testid: "nav-timeline" },
  { to: "/search", key: "nav.search", testid: "nav-search" },
  { to: "/detector", key: "nav.detector", testid: "nav-detector" },
  { to: "/settings", key: "nav.settings", testid: "nav-settings" },
];

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
      <div className="min-h-screen bg-canvas text-primary" data-testid="app">
        {/*
         * Sticky, translucent, and separated by a hairline rather than a filled
         * bar. The header was a white slab on a white page: it took the full
         * weight of a section without being one. Here it stays out of the way and
         * the content scrolls under it.
         */}
        <header className="sticky top-0 z-20 border-b border-line-subtle bg-canvas/85 backdrop-blur">
          <nav className="mx-auto flex max-w-5xl items-center gap-1 px-4 py-2.5">
            <span className="display me-4 text-sm" data-testid="app-title">
              {t("app.title")}
            </span>
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                data-testid={item.testid}
                /*
                 * The active tab is a filled pill, not an underline. An underline
                 * on a text link reads as "this is a link", which every item here
                 * already is; a filled shape reads as "you are here".
                 */
                className={({ isActive }) =>
                  `rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                    isActive
                      ? "bg-surface-2 font-medium text-primary"
                      : "text-secondary hover:bg-surface-1 hover:text-primary"
                  }`
                }
              >
                {t(item.key)}
              </NavLink>
            ))}
          </nav>
        </header>
        <ConnectionBanner />
        <RecordingBar />
        <DetectionNudge detection={detected} onDismiss={() => setDetected(null)} />
        <main className="mx-auto max-w-5xl px-4 py-8">
          <Routes>
            <Route path="/" element={<Timeline />} />
            <Route path="/m/:id" element={<MeetingPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/detector" element={<Detector />} />
          </Routes>
        </main>
      </div>
    </I18nContext.Provider>
  );
}
