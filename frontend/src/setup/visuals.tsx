import type { CSSProperties, ReactNode } from "react";
import { useI18n, type MessageKey } from "../i18n";
import type { CalendarPhase, SpeakerPhase } from "./backend";
import type { CaptureMode } from "./flow";
import "./setup.css";

/**
 * The pictures first-run setup explains itself with (epic z8tj1hb01k).
 *
 * Each scene shows one process — what installing does, what signing in looks like,
 * where the calendar's names go — so the words beside it can shrink to a line. They are
 * drawn in the app's own tokens, hold no text (so they mirror cleanly for Hebrew), and
 * stop moving for anyone who asked for reduced motion; the motion is in setup.css.
 */

const C = {
  accent: "var(--color-accent)",
  quiet: "var(--color-accent-quiet)",
  strong: "var(--color-line-strong)",
  line: "var(--color-line)",
  surface: "var(--color-surface-2)",
  surface3: "var(--color-surface-3)",
  raised: "var(--color-raised)",
  on: "var(--color-on-accent)",
  success: "var(--color-success)",
  successQuiet: "var(--color-success-quiet)",
  warning: "var(--color-warning)",
  tertiary: "var(--color-tertiary)",
};

const delay = (s: number): CSSProperties => ({ animationDelay: `${s}s` });

function Scene({ viewBox, className = "", children }: { viewBox: string; className?: string; children: ReactNode }) {
  return (
    <svg viewBox={viewBox} aria-hidden="true" className={`su-scene block ${className}`}>
      {children}
    </svg>
  );
}

/** A tick in a circle, the one sign of "done" used everywhere in setup. */
function Tick({ x, y, r = 9, className = "", style }: { x: number; y: number; r?: number; className?: string; style?: CSSProperties }) {
  return (
    <g className={className} style={style}>
      <circle cx={x} cy={y} r={r} fill={C.success} />
      <path
        d={`M${x - r * 0.45} ${y}l${r * 0.3} ${r * 0.32}l${r * 0.55} -${r * 0.62}`}
        fill="none"
        stroke={C.on}
        strokeWidth={r * 0.24}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
  );
}

/* ---------------------------------------------------------------- Welcome */

function Arrow() {
  return (
    <Scene viewBox="0 0 40 16" className="w-10 shrink-0 self-center">
      <path d="M2 8H32" stroke={C.strong} strokeWidth="2" className="su-flow" />
      <path d="M30 3l6 5-6 5" fill="none" stroke={C.strong} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </Scene>
  );
}

function Tile({ label, children }: { label: MessageKey; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <figure className="flex w-40 flex-col items-center gap-3">
      <div className="grid h-28 w-full place-items-center rounded-xl bg-raised shadow-sm">{children}</div>
      <figcaption className="text-center text-sm font-medium">{t(label)}</figcaption>
    </figure>
  );
}

/** Record → transcript → summary: the whole product in one loop. */
export function HeroFlow() {
  return (
    <div data-testid="scene-hero" className="flex flex-wrap items-start justify-center gap-2">
      <Tile label="firstRun.welcome.stage.record">
        <Scene viewBox="0 0 100 64" className="w-28">
          <rect x="14" y="10" width="18" height="30" rx="9" fill={C.accent} />
          <path d="M9 32a14 14 0 0 0 28 0M23 46v8M15 54h16" fill="none" stroke={C.accent} strokeWidth="3" strokeLinecap="round" />
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <rect
              key={i}
              x={50 + i * 8}
              y={16}
              width="4"
              height="32"
              rx="2"
              fill={C.accent}
              opacity={0.45 + (i % 3) * 0.2}
              className="su-bar"
              style={delay(i * 0.12)}
            />
          ))}
        </Scene>
      </Tile>
      <Arrow />
      <Tile label="firstRun.welcome.stage.transcribe">
        <Scene viewBox="0 0 100 64" className="w-28">
          {[0, 1, 2, 3].map((i) => (
            <rect
              key={i}
              x="14"
              y={12 + i * 12}
              width={i === 3 ? 44 : 72}
              height="5"
              rx="2.5"
              fill={i % 2 ? C.strong : C.tertiary}
              className="su-type"
              style={delay(0.6 + i * 0.35)}
            />
          ))}
        </Scene>
      </Tile>
      <Arrow />
      <Tile label="firstRun.welcome.stage.summarize">
        <Scene viewBox="0 0 100 64" className="w-28">
          {[0, 1, 2].map((i) => (
            <g key={i}>
              <Tick x={20} y={16 + i * 16} r={6} className="su-pop" style={delay(2.2 + i * 0.4)} />
              <rect x="32" y={13.5 + i * 16} width={i === 1 ? 40 : 56} height="5" rx="2.5" fill={C.strong} />
            </g>
          ))}
        </Scene>
      </Tile>
    </div>
  );
}

/* ---------------------------------------------------------------- Calendar */

/**
 * A calendar on one side, a recording on the other. Before connecting, the meeting's
 * name travels across to the recording, which is the whole point of the step; while
 * waiting, the line between them runs; once connected, the name stays put and ticks.
 */
export function CalendarScene({ phase }: { phase: CalendarPhase }) {
  const connected = phase === "connected";
  const waiting = phase === "waiting";
  return (
    <Scene viewBox="0 0 300 110" className="mx-auto w-full max-w-md">
      {/* The calendar. */}
      <rect x="8" y="10" width="96" height="90" rx="10" fill={C.raised} stroke={C.line} />
      <path d="M8 20a10 10 0 0 1 10-10h76a10 10 0 0 1 10 10v8H8z" fill={C.accent} />
      {[0, 1, 2].map((row) =>
        [0, 1, 2, 3].map((col) => (
          <rect key={`${row}-${col}`} x={18 + col * 20} y={38 + row * 20} width="12" height="10" rx="2" fill={C.surface3} />
        )),
      )}
      <rect x="18" y="56" width="52" height="14" rx="3" fill={C.quiet} stroke={C.accent} strokeWidth="1.5" />

      {/* The line between them. */}
      <path
        d="M110 55H190"
        stroke={connected ? C.accent : C.strong}
        strokeWidth="2.5"
        strokeDasharray={connected ? undefined : "4 4"}
        className={waiting ? "su-flow" : undefined}
      />

      {/* The recording. */}
      <rect x="196" y="10" width="96" height="90" rx="10" fill={C.raised} stroke={C.line} />
      {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
        <rect key={i} x={208 + i * 9} y={62 - (i % 4) * 5} width="4" height={16 + (i % 4) * 10} rx="2" fill={C.accent} opacity="0.55" />
      ))}
      <rect x="206" y="22" width="52" height="14" rx="3" fill={C.surface3} />

      {/* The meeting's name: carried across, or already there. */}
      {connected ? (
        <>
          <rect x="206" y="22" width="52" height="14" rx="3" fill={C.quiet} stroke={C.accent} strokeWidth="1.5" />
          <Tick x={282} y={20} r={10} />
        </>
      ) : (
        <rect
          x="18"
          y="56"
          width="52"
          height="14"
          rx="3"
          fill={C.accent}
          className="su-carry"
          style={{ "--su-dx": "188px", "--su-dy": "-34px" } as CSSProperties}
        />
      )}
    </Scene>
  );
}

/* ---------------------------------------------------------------- AI: install */

/** A window opens on the desktop, fills, closes by itself, and leaves the app ticked. */
export function InstallScene() {
  return (
    <Scene viewBox="0 0 240 120" className="w-full max-w-xs">
      <rect x="10" y="6" width="220" height="92" rx="8" fill={C.surface} stroke={C.line} />
      <path d="M100 98l-6 14h52l-6-14" fill={C.surface3} />
      <g className="su-window">
        <rect x="50" y="22" width="140" height="60" rx="6" fill={C.raised} stroke={C.strong} />
        <path d="M50 28a6 6 0 0 1 6-6h128a6 6 0 0 1 6 6v6H50z" fill={C.surface3} />
        <rect x="60" y="42" width="70" height="4" rx="2" fill={C.tertiary} />
        <rect x="60" y="51" width="48" height="4" rx="2" fill={C.tertiary} />
        <rect x="60" y="66" width="120" height="6" rx="3" fill={C.surface3} />
        <rect x="60" y="66" width="120" height="6" rx="3" fill={C.accent} className="su-fill" />
      </g>
      <g className="su-appear">
        <rect x="100" y="30" width="40" height="40" rx="10" fill={C.quiet} stroke={C.accent} strokeWidth="1.5" />
        <path d="M120 38l3 9 9 3-9 3-3 9-3-9-9-3 9-3z" fill={C.accent} />
        <Tick x={138} y={32} r={8} />
      </g>
    </Scene>
  );
}

/* ---------------------------------------------------------------- AI: sign in */

/**
 * The provider's page in the browser, the pointer pressing its button, and what comes
 * back to Upshot: the code to paste (Claude) or simply a tick (Codex).
 */
export function SignInScene({ code, intoWindow = false }: { code: boolean; intoWindow?: boolean }) {
  return (
    <Scene viewBox="0 0 290 120" className="w-full max-w-xs">
      {/* The browser. */}
      <rect x="6" y="8" width="160" height="104" rx="8" fill={C.raised} stroke={C.line} />
      <path d="M6 16a8 8 0 0 1 8-8h144a8 8 0 0 1 8 8v8H6z" fill={C.surface3} />
      {[0, 1, 2].map((i) => (
        <circle key={i} cx={16 + i * 8} cy="16" r="2.2" fill={C.strong} />
      ))}
      <rect x="44" y="12" width="110" height="8" rx="4" fill={C.raised} />
      <circle cx="86" cy="46" r="11" fill={C.surface3} />
      <rect x="52" y="64" width="68" height="4" rx="2" fill={C.tertiary} />
      <rect x="46" y="80" width="80" height="16" rx="5" fill={C.accent} className="su-press" />

      {/* The pointer. */}
      <g transform="translate(96 88)">
        <g className="su-pointer">
          <path d="M0 0l0 16 4.5-4 3 7 3-1.4-3-7 6-.4z" fill={C.raised} stroke="var(--color-primary)" strokeWidth="1.2" strokeLinejoin="round" />
        </g>
      </g>

      {intoWindow ? (
        <>
          {/* The window the install opened, waiting for the code. */}
          <rect x="196" y="8" width="88" height="104" rx="8" fill={C.surface} stroke={C.strong} />
          <path d="M196 16a8 8 0 0 1 8-8h72a8 8 0 0 1 8 8v8h-88z" fill={C.surface3} />
          <path d="M204 38l5 4-5 4" fill="none" stroke={C.tertiary} strokeWidth="2" strokeLinecap="round" />
          <rect x="214" y="40" width="44" height="4" rx="2" fill={C.tertiary} />
          <rect x="206" y="72" width="68" height="18" rx="4" fill={C.raised} stroke={C.line} />
        </>
      ) : (
        <>
          {/* Upshot. */}
          <rect x="196" y="8" width="88" height="104" rx="8" fill={C.raised} stroke={C.line} />
          <path d="M206 30c4 0 4-6 8-6s4 6 8 6 4-6 8-6" fill="none" stroke={C.accent} strokeWidth="2.5" />
          <rect x="206" y="36" width="24" height="2.5" fill={C.accent} />
          <rect x="206" y="72" width="68" height="18" rx="4" fill={C.surface} stroke={C.line} />
        </>
      )}

      {code ? (
        <g className="su-return" style={{ "--su-return": "150px" } as CSSProperties}>
          <rect x="62" y="74" width="54" height="14" rx="4" fill={C.quiet} stroke={C.accent} strokeWidth="1.2" />
          {[0, 1, 2, 3, 4].map((i) => (
            <circle key={i} cx={72 + i * 8.5} cy="81" r="2" fill={C.accent} />
          ))}
        </g>
      ) : (
        <g className="su-return" style={{ "--su-return": "150px" } as CSSProperties}>
          <Tick x={90} y={81} r={9} />
        </g>
      )}
    </Scene>
  );
}

/* ---------------------------------------------------------------- AI: stages */

export type Stage = "install" | "signin" | "ready";

const STAGES: { id: Stage; label: MessageKey }[] = [
  { id: "install", label: "firstRun.ai.stage.install" },
  { id: "signin", label: "firstRun.ai.stage.signin" },
  { id: "ready", label: "firstRun.ai.stage.ready" },
];

/** Install → Sign in → Ready, with where this card is now, and whether it stalled there. */
export function StageTrack({ current, failed = false, skipInstall = false }: { current: Stage; failed?: boolean; skipInstall?: boolean }) {
  const { t } = useI18n();
  const stages = skipInstall ? STAGES.slice(1) : STAGES;
  const at = stages.findIndex((s) => s.id === current);
  return (
    <ol data-testid="stage-track" data-stage={current} className="flex items-center gap-1.5 text-xs">
      {stages.map((stage, i) => {
        const done = i < at || (current === "ready" && i === at);
        const active = i === at && current !== "ready";
        return (
          <li key={stage.id} className="flex items-center gap-1.5">
            {i > 0 && <span aria-hidden="true" className={`h-0.5 w-6 rounded ${i <= at ? "bg-accent" : "bg-line"}`} />}
            <span
              aria-hidden="true"
              className={`grid size-5 place-items-center rounded-full text-[10px] font-medium ${
                done
                  ? "bg-success text-on-accent"
                  : active
                    ? failed
                      ? "bg-danger text-on-accent"
                      : "su-pulse bg-accent text-on-accent"
                    : "bg-surface-3 text-tertiary"
              }`}
            >
              {done ? "✓" : active && failed ? "!" : i + 1}
            </span>
            <span className={active || done ? "font-medium text-primary" : "text-tertiary"}>{t(stage.label)}</span>
          </li>
        );
      })}
    </ol>
  );
}

/* ---------------------------------------------------------------- Sound check */

const SEGMENTS = 20;

/** A speaker sending out waves while the test sound plays, and the level Upshot heard. */
export function SpeakerScene({ phase, level }: { phase: SpeakerPhase; level: number }) {
  const lit = Math.round(level * SEGMENTS);
  return (
    <div className="flex items-center gap-4">
      <Scene viewBox="0 0 48 48" className="size-14 shrink-0">
        <path d="M6 18h8l10-8v28l-10-8H6z" fill={phase === "silent" ? C.strong : C.accent} />
        {phase === "playing" &&
          [0, 1, 2].map((i) => (
            <path
              key={i}
              d={`M${29 + i * 5} ${17 - i * 3}a${8 + i * 5} ${8 + i * 5} 0 0 1 0 ${14 + i * 6}`}
              fill="none"
              stroke={C.accent}
              strokeWidth="2.5"
              strokeLinecap="round"
              className="su-wave"
              style={delay(i * 0.3)}
            />
          ))}
        {phase === "heard" && <Tick x={38} y={12} r={9} />}
        {phase === "silent" && (
          <g>
            <circle cx="38" cy="12" r="9" fill={C.warning} />
            <path d="M38 7.5v5.5M38 16.2v.3" stroke={C.on} strokeWidth="2.4" strokeLinecap="round" />
          </g>
        )}
      </Scene>
      <div className="flex h-3 flex-1 gap-0.5" role="meter" aria-valuenow={Math.round(level * 100)} aria-valuemin={0} aria-valuemax={100}>
        {Array.from({ length: SEGMENTS }, (_, i) => (
          <div
            key={i}
            className={`flex-1 rounded-[2px] transition-colors duration-75 ${
              i < lit ? (i > SEGMENTS * 0.8 ? "bg-warning" : "bg-accent") : "bg-surface-3"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- Recording */

/**
 * One picture per capture mode: a notification sliding in over a call, or a
 * recording that starts by itself. Only the chosen one moves.
 */
export function CaptureScene({ mode, active }: { mode: CaptureMode; active: boolean }) {
  const moving = (name: string) => (active ? name : undefined);
  return (
    <Scene viewBox="0 0 160 80" className="h-20 w-40">
      {mode === "shadow" && (
        <>
          {/* A call on screen: two people. */}
          <rect x="8" y="8" width="100" height="64" rx="6" fill={C.raised} stroke={C.line} />
          <circle cx="36" cy="34" r="9" fill={C.surface3} />
          <circle cx="80" cy="34" r="9" fill={C.surface3} />
          <rect x="24" y="48" width="24" height="10" rx="5" fill={C.surface3} />
          <rect x="68" y="48" width="24" height="10" rx="5" fill={C.surface3} />
          {/* The notification. */}
          <g className={moving("su-toast")}>
            <rect x="92" y="40" width="62" height="32" rx="6" fill={C.raised} stroke={C.accent} strokeWidth="1.5" />
            <path d="M104 62h10M106 62v-7a3 3 0 0 1 6 0v7" fill="none" stroke={C.accent} strokeWidth="1.8" strokeLinecap="round" />
            <circle cx="109" cy="52" r="1.2" fill={C.accent} />
            <rect x="120" y="50" width="28" height="3.5" rx="1.75" fill={C.strong} />
            <rect x="120" y="58" width="18" height="3.5" rx="1.75" fill={C.tertiary} />
          </g>
        </>
      )}
      {mode === "on" && (
        <>
          <circle cx="36" cy="40" r="14" fill="var(--color-danger-quiet)" />
          <circle cx="36" cy="40" r="7" fill="var(--color-danger)" className={moving("su-blink")} />
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <rect
              key={i}
              x={64 + i * 10}
              y="24"
              width="5"
              height="32"
              rx="2.5"
              fill={C.accent}
              opacity={0.5 + (i % 3) * 0.2}
              className={moving("su-bar")}
              style={delay(i * 0.1)}
            />
          ))}
        </>
      )}
    </Scene>
  );
}

/* ---------------------------------------------------------------- Done */

export function DoneMark({ className = "size-16" }: { className?: string }) {
  return (
    <Scene viewBox="0 0 64 64" className={className}>
      <circle cx="32" cy="32" r="30" fill={C.successQuiet} />
      <circle cx="32" cy="32" r="21" fill={C.success} />
      <path d="M22 33l7 7 14-15" fill="none" stroke={C.on} strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" className="su-draw" />
    </Scene>
  );
}
