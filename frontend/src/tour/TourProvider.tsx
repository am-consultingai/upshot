import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Joyride, EVENTS, STATUS, type Controls, type EventData, type Options, type Step } from "react-joyride";
import { api, type TourEventBody } from "../api";
import { useI18n } from "../i18n";
import { daysFor, rangeFor, startOfDay } from "../lib/calendar";
import { toast } from "../components/Toaster";
import { SAMPLE_MEETING_ID, SAMPLE_PREFIX, buildSample, isSampleId, pickSelector, type TourSample } from "./sample";
import { burn, onScreen } from "./burn";
import { waitFor } from "./wait";
import TourTooltip, { type TourStepData } from "./TourTooltip";

/**
 * The first-run tour (ClickUp z8tj1hmy14): four stops — record, the week, a meeting's
 * summary, AI setup — on a sample meeting, then the sample burns away.
 *
 * It starts on its own once, after first-run setup, and replays from the workspace menu
 * or Settings → About. Seen or skipped is `ui.tour_done`, a setting of its own: a
 * reinstall reopens setup (D69), and the tour must not come back with it. Esc skips; the
 * arrow keys step, mirrored right to left.
 *
 * The sample (./sample.ts) is made when the tour starts and dropped when it ends; the
 * server only hears counts, for its log (app/tour.py).
 */

type Origin = "setup" | "replay";

interface Tour {
  /** The tour is on screen, or its fire is still burning. */
  running: boolean;
  /** The sample, while the tour runs; null otherwise. */
  sample: TourSample | null;
  /** The stop on screen ("record", "calendar", "summary", "ai"), or null. */
  stop: string | null;
  start: (origin: Origin) => void;
}

const TourContext = createContext<Tour>({ running: false, sample: null, stop: null, start: () => undefined });

export function useTour(): Tour {
  return useContext(TourContext);
}

/** The tour's layer: above dialogs (80), below tooltips (90). */
const TOUR_LAYER = 85;

const OPTIONS: Partial<Options> = {
  skipBeacon: true,
  overlayClickAction: false,
  // Esc is ours: it skips the whole tour, where Joyride's would only close the step.
  dismissKeyAction: false,
  blockTargetInteraction: true,
  spotlightRadius: 10,
  spotlightPadding: 6,
  overlayColor: "rgb(0 0 0 / 45%)",
  zIndex: TOUR_LAYER,
  targetWaitTimeout: 6000,
  beforeTimeout: 8000,
  scrollOffset: 60,
  width: "auto",
  arrowColor: "var(--surface-raised)",
  backgroundColor: "var(--surface-raised)",
};

/** What burns at the end: everything marked as sample, and the calendar's sample blocks. */
const SAMPLE_PIECES = [
  "[data-tour-sample]",
  `[data-testid=calendar-timegrid] [data-event^="${SAMPLE_PREFIX}"]`,
  `[data-testid=calendar-timegrid] [data-meeting^="${SAMPLE_PREFIX}"]`,
].join(",");

/** The app's own chords (record, assistant, palette): the browser's meaning is held back too. */
const BROWSER_KEYS = new Set(["KeyR", "KeyJ", "KeyK"]);

function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

function log(body: TourEventBody): void {
  // A line in a log: losing one is not worth a word to the user.
  api.tourEvent(body).catch(() => undefined);
}

export default function TourProvider({ autoStart, children }: { autoStart: boolean; children: ReactNode }) {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const queryClient = useQueryClient();
  const [sample, setSample] = useState<TourSample | null>(null);
  const [origin, setOrigin] = useState<Origin>("setup");
  const [run, setRun] = useState(false);
  const [ending, setEnding] = useState(false);
  const [stop, setStop] = useState<string | null>(null);
  const controls = useRef<Controls | null>(null);
  const busy = useRef(false);
  const finishing = useRef(false);
  const path = useRef(pathname);
  path.current = pathname;

  const start = useCallback(
    (from: Origin) => {
      if (busy.current) return;
      busy.current = true;
      void (async () => {
        const today = startOfDay(new Date());
        const range = rangeFor("week", today);
        const safely = async <T,>(work: Promise<T>, fallback: T) => {
          try {
            return await work;
          } catch {
            return fallback;
          }
        };
        // The same queries the sidebar and the calendar make, so this mostly reads the cache.
        const [library, week, events] = await Promise.all([
          safely(queryClient.fetchQuery({ queryKey: ["meetings"], queryFn: () => api.meetings() }), { meetings: [], count: 0 }),
          safely(
            queryClient.fetchQuery({
              queryKey: ["meetings", range.from, range.to],
              queryFn: () => api.meetings({ from: range.from, to: range.to, limit: "500" }),
            }),
            { meetings: [], count: 0 },
          ),
          safely(
            queryClient.fetchQuery({
              queryKey: ["calendar-events", range.from, range.to],
              queryFn: () => api.calendarEvents(range.from, range.to),
            }),
            { events: [] },
          ),
        ]);
        const built = buildSample({
          now: new Date(),
          week: daysFor("week", today),
          library: library.meetings,
          weekEvents: events.events,
          weekMeetings: week.meetings,
          locale,
          t,
        });
        // In order: the start, then what it made.
        void api
          .tourEvent({ event: "started", origin: from })
          .catch(() => undefined)
          .finally(() =>
            log({
              event: "sample_created",
              recordings: built.meetings.length,
              events: built.events.length,
              own_events: built.ownInWeek,
              borrowed: built.borrowed,
            }),
          );
        setOrigin(from);
        setSample(built);
        // Only when elsewhere: a navigation here would clear the setup's arrival, and its
        // confetti with it, and leave a history entry that celebrates again.
        if (path.current !== "/") navigate("/");
        setRun(true);
      })();
    },
    [queryClient, locale, t, navigate],
  );

  /*
   * The ending, done or skipped: the server hears it, the fire burns the sample pieces on
   * screen, and the sample is dropped. The page stays blocked until the fire is out.
   */
  const finish = useCallback(
    async (outcome: "done" | "skipped", data: TourStepData | undefined) => {
      // Joyride reports the end more than once (the status, then the tour's end).
      if (!sample || finishing.current) return;
      finishing.current = true;
      setRun(false);
      setStop(null);
      setEnding(true);
      log(outcome === "done" ? { event: "ended", outcome } : { event: "ended", outcome, stop: data?.stop ?? 0, of: data?.of ?? 0 });
      void api
        .putSettings({ "ui.tour_done": true })
        .then(() => queryClient.invalidateQueries({ queryKey: ["settings"] }))
        .catch(() => undefined);
      // Joyride's overlay goes first, so the fire is seen on the page itself.
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const lit = onScreen([...document.querySelectorAll(SAMPLE_PIECES)]);
      await burn(lit);
      log({ event: "sample_removed", items: sample.meetings.length + sample.events.length, burnt: lit.length });
      setSample(null);
      controls.current = null;
      // Whatever burnt and is still on the page once the sample is gone is not a sample:
      // it gets its looks back (React does not reset styles it did not set).
      requestAnimationFrame(() => {
        for (const piece of lit) {
          if (!piece.isConnected) continue;
          for (const name of ["mask-image", "-webkit-mask-image", "visibility", "opacity", "transition"])
            piece.style.removeProperty(name);
        }
      });
      if (isSampleId(path.current.split("/m/")[1])) navigate("/", { replace: true });
      setEnding(false);
      finishing.current = false;
      busy.current = false;
      if (outcome === "skipped") toast({ title: t("tour.skipped") });
    },
    [sample, queryClient, navigate, t],
  );

  /*
   * After first-run setup: once, a moment after the welcome's confetti starts. Through a
   * ref, so the saved language arriving in that moment (which makes a new `start`) does
   * not cancel it.
   */
  const startRef = useRef(start);
  startRef.current = start;
  const autoStarted = useRef(false);
  useEffect(() => {
    if (!autoStart || autoStarted.current) return;
    autoStarted.current = true;
    window.setTimeout(() => startRef.current("setup"), 1200);
  }, [autoStart]);

  const rtl = locale === "he";
  const steps = useMemo<Step[]>(() => {
    if (!sample) return [];
    const toHome = async () => {
      if (path.current !== "/") navigate("/");
      await waitFor(() => document.querySelector("[data-testid=calendar-timegrid]"));
    };
    const pick = pickSelector(sample.pick);
    const stops: { id: string; data: Omit<TourStepData, "kind" | "stop" | "of">; step: Omit<Step, "content" | "data"> }[] = [
      {
        id: "record",
        data: { title: "tour.recordTitle", body: "tour.recordBody", keys: ["Ctrl", "R"] },
        step: { target: "[data-tour=record]", placement: rtl ? "left" : "right", before: toHome },
      },
      {
        id: "calendar",
        data: { title: "tour.calendarTitle", body: "tour.calendarBody" },
        step: {
          // The picked block; the whole week if it cannot be found.
          target: () =>
            (pick ? document.querySelector<HTMLElement>(pick) : null) ??
            document.querySelector<HTMLElement>("[data-testid=calendar-timegrid]"),
          placement: "auto",
          // The grid scrolls inside itself under a sticky row of day names, which Joyride's
          // own scroll put the block behind: it is centred here instead.
          skipScroll: true,
          before: async () => {
            await toHome();
            // Within Joyride's own wait for this hook (beforeTimeout).
            if (pick) await waitFor(() => document.querySelector(pick), 1500);
            if (pick) document.querySelector(pick)?.scrollIntoView({ block: "center", inline: "nearest" });
          },
        },
      },
      {
        id: "summary",
        data: { title: "tour.summaryTitle", body: "tour.summaryBody" },
        step: {
          target: "[data-tour=sample-summary]",
          placement: "auto",
          before: async () => {
            navigate(`/m/${SAMPLE_MEETING_ID}`);
            await waitFor(() => document.querySelector("[data-tour=sample-summary]"));
          },
        },
      },
      {
        id: "ai",
        data: { title: "tour.aiTitle", body: "tour.aiBody" },
        step: {
          // The subscriptions, once their status is in; the whole list until then.
          target: () =>
            document.querySelector<HTMLElement>("[data-testid=provider-group]") ??
            document.querySelector<HTMLElement>("[data-testid=provider-settings]"),
          placement: "bottom",
          before: async () => {
            navigate("/settings#summaries");
            await waitFor(() => document.querySelector("[data-testid=provider-settings]"));
            await waitFor(() => document.querySelector("[data-testid=provider-group]"), 2500);
          },
        },
      },
    ];
    const welcome: Step[] =
      origin === "setup"
        ? [
            {
              id: "welcome",
              target: "body",
              placement: "center",
              content: null,
              data: {
                kind: "welcome",
                title: "tour.welcomeTitle",
                body: "tour.welcomeBody",
                stops: ["tour.stopRecord", "tour.stopCalendar", "tour.stopSummary", "tour.stopAi"],
                of: stops.length,
              } satisfies TourStepData,
            },
          ]
        : [];
    return [
      ...welcome,
      ...stops.map(({ id, data, step }, index) => ({
        ...step,
        id,
        content: null,
        data: { ...data, kind: "stop", stop: index + 1, of: stops.length } satisfies TourStepData,
      })),
    ];
  }, [sample, origin, rtl, navigate]);

  const onEvent = useCallback(
    (data: EventData, given: Controls) => {
      controls.current = given;
      if (data.type === EVENTS.TOOLTIP) setStop(typeof data.step?.id === "string" ? data.step.id : null);
      if (data.type !== EVENTS.TOUR_END && data.type !== EVENTS.TOUR_STATUS) return;
      if (data.status === STATUS.FINISHED) void finish("done", data.step?.data as TourStepData | undefined);
      else if (data.status === STATUS.SKIPPED) void finish("skipped", data.step?.data as TourStepData | undefined);
    },
    [finish],
  );

  /*
   * While the tour is up it owns the keyboard: Esc skips, the arrows step (mirrored right
   * to left), and nothing reaches the app underneath — Ctrl+R must not start a recording
   * from behind the tour. The card's own buttons still take Enter, Space and Tab; no
   * shortcut chord gets through even from there, and Tab outside the card goes back into it.
   */
  useEffect(() => {
    if (!run && !ending) return undefined;
    const onKey = (event: KeyboardEvent) => {
      const inTip = event.target instanceof Element && event.target.closest("[data-testid=tour-tip],[data-testid=tour-welcome]");
      if (ending) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const given = controls.current;
      const forward = rtl ? "ArrowLeft" : "ArrowRight";
      const backward = rtl ? "ArrowRight" : "ArrowLeft";
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        // Before Joyride's first event there are no controls to skip with yet.
        if (given) given.skip("button_skip");
        // The card that is opening is the first one: the welcome after setup, else stop 1.
        else void finish("skipped", steps[0]?.data as TourStepData | undefined);
      } else if (event.key === forward) {
        event.preventDefault();
        event.stopPropagation();
        given?.next();
      } else if (event.key === backward) {
        event.preventDefault();
        event.stopPropagation();
        const first = origin === "setup" ? 1 : 0;
        if (given && given.info().index > first) given.prev();
      } else {
        const chord = event.ctrlKey || event.metaKey || event.altKey;
        if (inTip && !chord) return;
        event.stopPropagation();
        // A chord reaches no app shortcut now; only the browser's own on the app's keys are
        // held back (reload, downloads, search), so zoom still works during the tour.
        if (!chord || BROWSER_KEYS.has(event.code)) event.preventDefault();
        if (!chord && event.key === "Tab") {
          document
            .querySelector("[data-testid=tour-tip],[data-testid=tour-welcome]")
            ?.querySelector<HTMLElement>("button[data-testid=tour-next]")
            ?.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [run, ending, rtl, origin, finish, steps]);

  const value = useMemo<Tour>(
    () => ({ running: run || ending, sample, stop, start }),
    [run, ending, sample, stop, start],
  );

  return (
    <TourContext.Provider value={value}>
      {children}
      {sample && steps.length > 0 && (
        <Joyride
          steps={steps}
          run={run}
          continuous
          options={OPTIONS}
          tooltipComponent={TourTooltip}
          // Joyride fades the card in whatever the system asks; reduced motion means none.
          styles={{ floater: { transition: reducedMotion() ? "none" : "opacity 0.3s" } }}
          locale={{ back: t("tour.back"), close: t("tour.skip"), last: t("tour.done"), next: t("tour.next"), skip: t("tour.skip") }}
          onEvent={onEvent}
        />
      )}
      {/* Nothing under the fire can be pressed while it burns. */}
      {ending && <div data-testid="tour-blocker" className="fixed inset-0" style={{ zIndex: TOUR_LAYER }} />}
    </TourContext.Provider>
  );
}
