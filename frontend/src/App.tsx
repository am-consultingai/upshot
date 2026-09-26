import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { I18nContext, applyLocale, catalogues, type Locale, type MessageKey } from "./i18n";
import { applyTheme, isTheme, type Theme } from "./theme";
import Library from "./routes/Library";
import MeetingPage from "./routes/Meeting";
import SearchPage from "./routes/Search";
import ActionsPage from "./routes/Actions";
import Settings from "./routes/Settings";
import Welcome from "./routes/Welcome";
import { setupPending } from "./lib/speech";
import Sidebar from "./components/Sidebar";
import RecordingBar from "./components/RecordingBar";
import DetectionNudge, { type Detection } from "./components/DetectionNudge";
import ConnectionBanner from "./components/ConnectionBanner";
import CommandPalette from "./components/CommandPalette";
import Toaster from "./components/Toaster";
import ConfirmHost from "./components/ConfirmDialog";
import AssistantPanel from "./components/assistant/AssistantPanel";
import AssistantLauncher from "./components/assistant/AssistantLauncher";
import Confetti from "./components/Confetti";
import { useRecordingControls } from "./lib/recording";

/*
 * The first-run setup mock (Setup 0, z8tj1hb03a), for confirming the flow before it is
 * built. `vite dev`, or a build made with VITE_SETUP_MOCK=1, and nothing else: with the
 * flag false the import is dead code and the chunk never reaches the frozen app.
 */
const SETUP_MOCK = import.meta.env.DEV || import.meta.env.VITE_SETUP_MOCK === "1";
const MockSetup = SETUP_MOCK ? lazy(() => import("./setup/MockSetup")) : null;

/** Typing into a field is not a shortcut. */
function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

/**
 * The keys the interface advertises, made true.
 *
 * The sidebar shows `/` beside Search and `Ctrl R` on the record button, and the
 * palette teaches `G L`, `G A`, `G ,`. A hint for a key that does nothing is worse
 * than no hint: it teaches the reader that the hints are decoration. Two-key
 * sequences follow Gmail and Linear — G, then the destination, within a second.
 */
function useShortcuts(onRecord: () => void) {
  const navigate = useNavigate();
  const pendingG = useRef<number | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // The physical key, as Ctrl+J does: on a Hebrew layout event.key is "ר", which
      // missed this and let the browser reload the page instead.
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.code === "KeyR") {
        event.preventDefault();
        onRecord();
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return;
      if (document.querySelector("[role=dialog],[role=alertdialog],[role=menu]")) return;
      if (pendingG.current !== null) {
        window.clearTimeout(pendingG.current);
        pendingG.current = null;
        const to = { l: "/", a: "/actions", s: "/search", ",": "/settings" }[event.key.toLowerCase()];
        if (to) {
          event.preventDefault();
          navigate(to);
        }
        return;
      }
      if (event.key === "/") {
        event.preventDefault();
        navigate("/search");
      } else if (event.key.toLowerCase() === "g") {
        pendingG.current = window.setTimeout(() => {
          pendingG.current = null;
        }, 1000);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigate, onRecord]);
}

/**
 * The assistant opens and closes with Ctrl/Cmd+J (D62), as Linear's and Fireflies' do.
 *
 * Matched on `event.code`, the physical key, so it still works when the keyboard is
 * set to Hebrew and `event.key` is "ח". Closing gives focus back to whatever had it
 * when the panel opened. `upshot:assistant` on the window toggles it too.
 */
function useAssistantToggle() {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const close = useCallback(() => {
    setOpen(false);
    const back = opener.current;
    opener.current = null;
    window.setTimeout(() => back?.focus(), 0);
  }, []);
  const toggle = useCallback(() => {
    setOpen((was) => {
      if (was) {
        const back = opener.current;
        opener.current = null;
        window.setTimeout(() => back?.focus(), 0);
        return false;
      }
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      return true;
    });
  }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.code === "KeyJ") {
        // The browser's own Ctrl+J is Downloads.
        event.preventDefault();
        toggle();
        return;
      }
      if (event.key === "Escape" && document.querySelector("[data-testid=assistant-panel]")?.contains(event.target as Node)) {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("upshot:assistant", toggle);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("upshot:assistant", toggle);
    };
  }, [toggle, close]);
  return { open, close, toggle };
}

/**
 * A screen that is not one item from the library: it gets the whole width.
 *
 * `wide` is for a screen that carries its own navigation. Settings does, and a
 * centred 896px column left its section list floating in the middle of the
 * window with a void to the left of it — the nav has to start where the content
 * area starts. Microsoft puts a settings page at 1000-1100px, which is what
 * `max-w-5xl` is.
 */
function Full({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="min-w-0 flex-1 overflow-y-auto">
      {/*
       * Anchored to the start edge, not centred. `mx-auto max-w-4xl` is a marketing
       * measure applied to application chrome: on a 1440px window it left ~250px of
       * nothing on either side of every screen, which is the single loudest thing
       * about the old layout. The cap stays — a list still should not run to 1400px
       * — but the leftover width belongs to the page, not to the margins.
       */}
      <div className={`px-8 py-7 ${wide ? "max-w-5xl" : "max-w-3xl"}`}>{children}</div>
    </div>
  );
}

export default function App() {
  const [locale, setLocale] = useState<Locale>("en");
  const [theme, setTheme] = useState<Theme>("light");
  const [tooltips, setTooltips] = useState(true);
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
    | { ui?: { language?: string; theme?: string; tooltips_off?: boolean } }
    | undefined;
  useEffect(() => {
    const language = savedConfig?.ui?.language;
    if (language === "en" || language === "he") setLocale(language);
  }, [savedConfig?.ui?.language]);
  useEffect(() => {
    setTooltips(savedConfig?.ui?.tooltips_off !== true);
  }, [savedConfig?.ui?.tooltips_off]);
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
    // The connection finishes in another tab — Google's — so Settings learns of it here.
    source.addEventListener("calendar", invalidate);
    // The detector's verdicts go to the log file and to `detector_events`; what the
    // interface still needs from them is the nudge, which is a different thing from
    // the Detector screen removed on 2026-09-22.
    source.addEventListener("detector", (event) => {
      invalidate();
      const payload = JSON.parse((event as MessageEvent).data) as Detection & {
        state?: string;
      };
      // "shadow" is the verdict reached while only watching: a meeting we are not
      // recording. In automatic mode the recording has already started and the bar says so.
      // "upcoming" is a calendar meeting starting with nothing recording it.
      if (payload.state === "shadow" || payload.state === "upcoming") setDetected(payload);
    });
    return () => {
      source.close();
      delete document.documentElement.dataset.stream;
    };
  }, [queryClient]);

  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  // Ctrl+R starts a recording, and stops the one running: the same key both ways.
  const recording = useRecordingControls();
  const active = status.data?.recorder.active ?? false;
  const recordNow = useCallback(() => {
    if (active) recording.stop.mutate();
    else recording.start.mutate();
  }, [active, recording.start, recording.stop]);
  useShortcuts(recordNow);

  const t = useCallback((key: MessageKey) => catalogues[locale][key], [locale]);
  const value = useMemo(
    () => ({ locale, t, setLocale, theme, setTheme, tooltips, setTooltips }),
    [locale, t, theme, tooltips],
  );

  /*
   * First-run setup comes first until it is done or skipped. Only once the settings
   * have loaded and say so — never on a guess — so a returning user's library does
   * not flash past on the way to a screen they have already finished.
   *
   * The welcome screen has the window to itself: no rail, no capture question, no
   * detection nudge. Each of those assumes an app that is already set up.
   */
  const { pathname, state: routeState } = useLocation();
  const navigateTo = useNavigate();
  // First-run setup just finished: celebrate once, then forget it (a reload keeps
  // history state, and the confetti is for the first arrival only).
  const celebrate = (routeState as { celebrate?: boolean } | null)?.celebrate === true;
  const celebrated = useCallback(() => navigateTo(pathname, { replace: true, state: null }), [navigateTo, pathname]);
  const welcoming = pathname === "/welcome" || pathname === "/setup-mock";
  const assistant = useAssistantToggle();
  const sendToSetup = saved.isSuccess && setupPending(saved.data?.config) && !welcoming;

  return (
    <I18nContext.Provider value={value}>
      <div className="flex h-screen overflow-hidden bg-canvas text-primary" data-testid="app">
        <CommandPalette />
        <Toaster />
        {celebrate && <Confetti onDone={celebrated} />}
        <ConfirmHost />
        {!welcoming && <Sidebar />}
        <div className="flex min-w-0 flex-1 flex-col">
          <ConnectionBanner />
          <RecordingBar />
          {!welcoming && (
            <DetectionNudge detection={detected} onDismiss={() => setDetected(null)} />
          )}
          {/* How meetings are recorded is asked in first-run setup (D64), not over the library. */}
          <main className="flex min-h-0 flex-1">
            {sendToSetup ? (
              <Navigate to="/welcome" replace />
            ) : (
            <Routes>
              <Route path="/welcome" element={<Welcome />} />
              {MockSetup && (
                <Route
                  path="/setup-mock"
                  element={<Suspense fallback={null}><MockSetup /></Suspense>}
                />
              )}
              {/*
               * The library owns the list; what you open renders beside it. Search,
               * settings are a whole screen rather than one item from a
               * collection, so it takes the full width instead.
               */}
              <Route element={<Library />}>
                {/*
                 * The index renders nothing of its own: with no meeting open, Library
                 * gives the detail side to the calendar and never reaches the Outlet.
                 * It used to be a trimmed copy of the action-item inbox — one screen's
                 * worth of the rows the inbox already holds and the rail already
                 * reaches. The route itself has to stay, or "/" matches nothing and the
                 * library does not render at all.
                 */}
                <Route path="/" element={<></>} />
                <Route path="/m/:id" element={<MeetingPage />} />
              </Route>
              {/* The inbox carries its own bar and rail, like a meeting, so it is not in a Full column. */}
              <Route path="/actions" element={<ActionsPage />} />
              <Route path="/search" element={<Full><SearchPage /></Full>} />
              <Route path="/settings" element={<Full wide><Settings /></Full>} />
              {/*
                * Anything else goes home rather than rendering an empty pane. There
                * was no catch-all while every path in the rail had a route; removing
                * the Detector screen on 2026-09-22 made `/detector` — which people
                * may have bookmarked, and which the tray could still hold — resolve
                * to nothing at all.
                */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            )}
          </main>
        </div>
        {!welcoming && assistant.open && <AssistantPanel onClose={assistant.close} />}
        {!welcoming && <AssistantLauncher open={assistant.open} onToggle={assistant.toggle} />}
      </div>
    </I18nContext.Provider>
  );
}
