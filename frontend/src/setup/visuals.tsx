import type { CSSProperties, ReactNode } from "react";
import { directionFor, useI18n, type MessageKey } from "../i18n";
import type { CalendarPhase, SpeakerPhase } from "./backend";
import type { CaptureMode, CliId } from "./flow";
import "./setup.css";
import { VendorLogo } from "./logos";
import { fill } from "./ui";

/**
 * The pictures first-run setup explains itself with (epic z8tj1hb01k).
 *
 * Each scene shows one process — what installing does, what signing in looks like,
 * where the calendar's names go — so the words beside it can shrink to a line. They are
 * drawn in the app's own tokens and hold no text (so they mirror cleanly for Hebrew);
 * the motion is in setup.css.
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

/** Google's "G", in its own colours: the one mark that says "this is Google's page". */
function GoogleG({ size = 18 }: { size?: number }) {
  return (
    <svg viewBox="0 0 48 48" width={size} height={size} aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

/** The pointer, its tip at the top-left corner of its box. */
function Pointer({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 16 20" width="16" height="20" aria-hidden="true" className={`absolute start-0 top-0 ${className}`}>
      <path d="M1 1l0 16 4.5-4 3 7 3-1.4-3-7 6-.4z" fill="white" stroke="#1f2328" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Connecting Google Calendar, as the user does it (D73): press Connect with Google in
 * Upshot; Google's page opens in the browser; choose the account; on Google's consent
 * screen, which asks to "View events on all your calendars", press Continue; the browser
 * closes and Upshot shows the calendar connected. One ten-second loop (setup.css,
 * "su-g-"). Once connected, only the last frame shows, with the real account.
 *
 * Unlike the other scenes this one holds words, because Google's pages are recognised by
 * them. The stage is laid out left to right in every language (the pointer's path is
 * fixed), and each text follows its own direction.
 */
/** The example address on Google's page before a real one is known: data, not text. */
const SAMPLE_EMAIL = "you@gmail.com";

/**
 * Connecting Google Calendar, as the user does it (D73): press Connect with Google in
 * Upshot; that opens a second window, the browser, on Google's sign-in; type the email
 * and press Next; on Google's consent screen, which asks to "View events on all your
 * calendars", press Continue; the browser closes and Upshot shows the calendar
 * connected. One eleven-second loop (setup.css, "su-g-"). Once connected, only Upshot's
 * card shows, centred, with the real account.
 *
 * Unlike the other scenes this one holds words, because Google's pages are recognised by
 * them. The stage is laid out left to right in every language (the pointer's path is
 * fixed), and each line takes the interface's direction.
 */
export function CalendarScene({ phase, account }: { phase: CalendarPhase; account?: string }) {
  const { t, locale } = useI18n();
  // "Upshot wants…" in Hebrew starts with a Latin word, and dir="auto" would lay the
  // whole sentence out left to right.
  const dir = directionFor(locale);
  const connected = phase === "connected";
  const email = account || SAMPLE_EMAIL;

  const titleBar = (
    <div className="flex h-7 items-center gap-1.5 bg-surface-3 px-2.5">
      <svg viewBox="0 0 24 12" width="16" height="8" aria-hidden="true">
        <path d="M1 7c2.5 0 2.5-5 5-5s2.5 5 5 5 2.5-5 5-5 2.5 5 5 5" fill="none" stroke="var(--color-accent)" strokeWidth="2.2" />
      </svg>
      <span className="text-[11px] font-semibold">{t("app.title")}</span>
    </div>
  );
  const connectedCard = (animated: boolean) => (
    <div className={`absolute inset-x-3 bottom-3 top-10 flex flex-col gap-2 ${animated ? "su-g-done" : ""}`}>
      <div className="flex items-center gap-2 rounded-lg bg-success-quiet px-2.5 py-2 text-xs font-semibold text-success">
        <span className="grid size-5 shrink-0 place-items-center rounded-full bg-success text-[11px] text-on-accent">✓</span>
        <span dir={dir} className="truncate">{t("firstRun.calendar.scene.connected")}</span>
      </div>
      <span dir="auto" className="truncate px-1 text-[11px] text-tertiary">{email}</span>
      {["09:30", "14:00"].map((time, i) => (
        <div key={time} className="flex items-center gap-2 rounded-md border border-line px-2 py-1.5 text-[11px]">
          <span className="font-mono text-tertiary">{time}</span>
          <span className="h-1.5 flex-1 rounded-full bg-line-strong" style={{ maxWidth: i ? 56 : 80 }} />
        </div>
      ))}
    </div>
  );

  if (connected) {
    return (
      <div data-testid="scene-calendar" data-phase={phase} className="relative mx-auto h-[200px] w-[190px]" aria-hidden="true">
        <div className="absolute inset-0 overflow-hidden rounded-xl border border-line bg-raised shadow-sm">
          {titleBar}
          {connectedCard(false)}
        </div>
      </div>
    );
  }

  return (
    <div data-testid="scene-calendar" data-phase={phase} dir="ltr" className="relative mx-auto h-[250px] w-[560px] max-w-full select-none" aria-hidden="true">
      {/* 1. Upshot, with its Connect button. */}
      <div className="absolute start-0 top-[25px] h-[200px] w-[190px] overflow-hidden rounded-xl border border-line bg-raised shadow-sm">
        {titleBar}
        <div className="su-g-ask absolute inset-x-3 top-10 flex flex-col gap-1.5">
          <span dir={dir} className="text-[12px] font-semibold leading-tight">{t("firstRun.calendar.title")}</span>
          <span dir={dir} className="text-[10px] leading-snug text-tertiary">{t("firstRun.calendar.lead")}</span>
        </div>
        <div className="su-g-connect absolute start-3.5 top-[150px] flex h-[30px] w-[162px] items-center justify-center gap-2 rounded-md bg-accent text-[11px] font-semibold text-on-accent">
          <span className="grid size-4 place-items-center rounded-sm bg-[#fff]">
            <GoogleG size={11} />
          </span>
          <span dir={dir}>{t("firstRun.calendar.connect")}</span>
        </div>
        {connectedCard(true)}
      </div>

      {/* 2. Pressing it opens another window: the browser. */}
      <svg viewBox="0 0 70 120" width="70" height="120" className="absolute start-[184px] top-[62px] overflow-visible" aria-hidden="true">
        <path d="M4 112 C 30 112, 36 20, 62 12" fill="none" stroke="var(--color-accent)" strokeWidth="2" strokeLinecap="round" className="su-g-link" />
        <path d="M54 6 L 64 11 L 57 20" fill="none" stroke="var(--color-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="su-g-link-head" />
      </svg>
      <span dir={dir} className="su-g-link-label absolute start-[193px] top-[26px] w-[54px] text-center text-[9.5px] font-medium leading-tight text-accent">
        {t("firstRun.calendar.scene.opens")}
      </span>

      <div className="su-g-browser absolute start-[250px] top-0 h-[250px] w-[310px] overflow-hidden rounded-xl border border-line-strong bg-[#fff] text-[#1f1f1f] shadow-md">
        <div className="flex h-[26px] items-center gap-1.5 bg-[#f1f3f4] px-2.5">
          {[0, 1, 2].map((i) => (
            <span key={i} className="size-2 rounded-full bg-[#c4c7c5]" />
          ))}
          <span className="ms-2 flex h-4 flex-1 items-center rounded-full bg-[#fff] px-2 text-[9px] text-[#444746]">🔒 accounts.google.com</span>
        </div>

        {/* 3. Google's sign-in: the email, then Next. */}
        <div className="su-g-signin absolute inset-x-0 bottom-0 top-[26px] px-5 pt-4">
          <GoogleG size={22} />
          <p dir={dir} className="mt-2 text-[16px]">{t("firstRun.calendar.scene.signIn")}</p>
          <p dir={dir} className="text-[10px] text-[#444746]">{t("firstRun.calendar.scene.toContinue")}</p>
          <div className="mt-4 rounded-md border-2 border-[#1a73e8] px-2.5 pb-1.5 pt-1">
            <span dir={dir} className="block text-[8.5px] text-[#1a73e8]">{t("firstRun.calendar.scene.emailLabel")}</span>
            <span className="su-g-type block overflow-hidden whitespace-nowrap text-[11px]">{email}</span>
          </div>
          <span dir={dir} className="su-g-next absolute bottom-4 end-4 rounded-full bg-[#1a73e8] px-4 py-1.5 text-[10.5px] font-medium text-[#fff]">
            {t("firstRun.calendar.scene.next")}
          </span>
        </div>

        {/* 4. Consent: what Upshot may see, then Continue. */}
        <div className="su-g-consent absolute inset-x-0 bottom-0 top-[26px] px-5 pt-3">
          <div className="flex items-center gap-2">
            <GoogleG size={16} />
            <span dir="auto" className="rounded-full border border-[#e3e3e3] px-2 py-0.5 text-[9.5px] text-[#444746]">{email}</span>
          </div>
          <p dir={dir} className="mt-2 text-[13px] leading-snug">{t("firstRun.calendar.scene.wants")}</p>
          <p dir={dir} className="mt-2 text-[10px] text-[#444746]">{t("firstRun.calendar.scene.allow")}</p>
          <div className="mt-1.5 flex items-center gap-2 rounded-lg border border-[#e3e3e3] px-2.5 py-2">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <rect x="3" y="4" width="18" height="17" rx="2" fill="#fff" stroke="#1a73e8" strokeWidth="1.8" />
              <path d="M3 8h18" stroke="#1a73e8" strokeWidth="1.8" />
              <text x="12" y="18.5" textAnchor="middle" fontSize="8" fontWeight="700" fill="#1a73e8">31</text>
            </svg>
            <span dir={dir} className="flex-1 text-[10.5px]">{t("firstRun.calendar.scene.permission")}</span>
            <span className="grid size-4 place-items-center rounded-[3px] bg-[#1a73e8] text-[10px] text-[#fff]">✓</span>
          </div>
          <p dir={dir} className="mt-2 text-[9px] leading-snug text-[#444746]">{t("firstRun.calendar.scene.trust")}</p>
          <div className="absolute bottom-3 end-4 flex gap-2">
            <span dir={dir} className="rounded-full px-3 py-1.5 text-[10.5px] font-medium text-[#1a73e8]">{t("firstRun.calendar.scene.cancel")}</span>
            <span dir={dir} className="su-g-continue rounded-full bg-[#1a73e8] px-3.5 py-1.5 text-[10.5px] font-medium text-[#fff]">
              {t("firstRun.calendar.scene.continue")}
            </span>
          </div>
        </div>
      </div>

      <Pointer className="su-g-pointer" />
    </div>
  );
}

/**
 * Signing in to Claude or ChatGPT, as the user does it (D75): press Sign in in Upshot;
 * that opens the browser on the vendor's page; log in and press Continue; allow the
 * access; for Claude, copy the code it shows, which goes into Upshot; the browser closes
 * and Upshot shows the service ready. One twelve-second loop (setup.css, "su-v-"), laid
 * out like the calendar scene, words from the catalogue.
 */
export function VendorSignInScene({ vendor }: { vendor: CliId }) {
  const { t, locale } = useI18n();
  const dir = directionFor(locale);
  const page = SCENE[vendor];
  // Claude and Google end on a code to paste into Upshot; ChatGPT finishes by itself.
  const code = vendor !== "codex";
  const name = t(page.name);
  const ink = page.ink;
  const button = (label: string, cls: string) => (
    <span dir={dir} className={`${cls} absolute bottom-4 end-4 rounded-full px-4 py-1.5 text-[10.5px] font-medium text-[#fff]`} style={{ background: ink }}>
      {label}
    </span>
  );
  return (
    <div data-testid={`scene-signin-${vendor}`} dir="ltr" className="relative mx-auto h-[250px] w-[560px] max-w-full select-none" aria-hidden="true">
      {/* 1. Upshot, with its Sign in button. */}
      <div className="absolute start-0 top-[25px] h-[200px] w-[190px] overflow-hidden rounded-xl border border-line bg-raised shadow-sm">
        <div className="flex h-7 items-center gap-1.5 bg-surface-3 px-2.5">
          <svg viewBox="0 0 24 12" width="16" height="8" aria-hidden="true">
            <path d="M1 7c2.5 0 2.5-5 5-5s2.5 5 5 5 2.5-5 5-5 2.5 5 5 5" fill="none" stroke="var(--color-accent)" strokeWidth="2.2" />
          </svg>
          <span className="text-[11px] font-semibold">{t("app.title")}</span>
        </div>
        <div className="absolute inset-x-3 top-10 flex items-center gap-2">
          <VendorLogo vendor={vendor} size={22} />
          <span className="text-[12px] font-semibold">{name}</span>
        </div>
        <div className="su-v-ask absolute inset-x-3 top-[72px]">
          <span dir={dir} className="block text-[10px] leading-snug text-tertiary">{t("firstRun.ai.scene.ask")}</span>
        </div>
        <div className="su-v-connect absolute start-3.5 top-[150px] flex h-[30px] w-[162px] items-center justify-center rounded-md bg-accent text-[11px] font-semibold text-on-accent">
          <span dir={dir}>{fill(t("firstRun.ai.signinButton"), { name })}</span>
        </div>
        <div className="su-v-done absolute inset-x-3 top-[72px] flex flex-col gap-2">
          {code && (
            <div className="flex items-center gap-2 rounded-md border border-line px-2 py-1.5 text-[10px]">
              <span className="font-mono text-tertiary">8fJk…#Qw3…</span>
              <span dir={dir} className="ms-auto text-success">{t("firstRun.ai.scene.pasted")}</span>
            </div>
          )}
          <div className="flex items-center gap-2 rounded-lg bg-success-quiet px-2.5 py-2 text-xs font-semibold text-success">
            <span className="grid size-5 shrink-0 place-items-center rounded-full bg-success text-[11px] text-on-accent">✓</span>
            <span dir={dir}>{t("firstRun.ai.ready")}</span>
          </div>
        </div>
      </div>

      {/* 2. Pressing it opens the browser. */}
      <svg viewBox="0 0 70 120" width="70" height="120" className="absolute start-[184px] top-[62px] overflow-visible" aria-hidden="true">
        <path d="M4 112 C 30 112, 36 20, 62 12" fill="none" stroke="var(--color-accent)" strokeWidth="2" strokeLinecap="round" className="su-v-link" />
        <path d="M54 6 L 64 11 L 57 20" fill="none" stroke="var(--color-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="su-v-link-head" />
      </svg>
      <span dir={dir} className="su-v-link-head absolute start-[193px] top-[26px] w-[54px] text-center text-[9.5px] font-medium leading-tight text-accent">
        {t("firstRun.calendar.scene.opens")}
      </span>

      <div className="su-v-browser absolute start-[250px] top-0 h-[250px] w-[310px] overflow-hidden rounded-xl border border-line-strong bg-[#fff] text-[#1f1f1f] shadow-md">
        <div className="flex h-[26px] items-center gap-1.5 bg-[#f1f3f4] px-2.5">
          {[0, 1, 2].map((i) => (
            <span key={i} className="size-2 rounded-full bg-[#c4c7c5]" />
          ))}
          <span className="ms-2 flex h-4 flex-1 items-center rounded-full bg-[#fff] px-2 text-[9px] text-[#444746]">
            🔒 {page.domain}
          </span>
        </div>

        {/* 3. The vendor's log-in page: the email, then Continue. */}
        <div className="su-v-page1 absolute inset-x-0 bottom-0 top-[26px] px-5 pt-4">
          <VendorLogo vendor={vendor} size={22} />
          <p dir={dir} className="mt-2 text-[16px]">{t(page.login)}</p>
          <div className="mt-4 rounded-md border-2 px-2.5 pb-1.5 pt-1" style={{ borderColor: ink }}>
            <span dir={dir} className="block text-[8.5px] text-[#444746]">{t("firstRun.calendar.scene.emailLabel")}</span>
            <span className="su-v-type block overflow-hidden whitespace-nowrap text-[11px]">{SAMPLE_EMAIL}</span>
          </div>
          {button(t("firstRun.calendar.scene.continue"), "su-v-press1")}
        </div>

        {/* 4. Allow the access. */}
        <div className="su-v-page2 absolute inset-x-0 bottom-0 top-[26px] px-5 pt-4">
          <VendorLogo vendor={vendor} size={22} />
          <p dir={dir} className="mt-2 text-[13px] leading-snug">{t(page.allow)}</p>
          <p dir="auto" className="mt-2 inline-block rounded-full border border-[#e3e3e3] px-2 py-0.5 text-[9.5px] text-[#444746]">{SAMPLE_EMAIL}</p>
          {button(t(page.allowButton), "su-v-press2")}
        </div>

        {/* 5. Claude and Google: the code to copy into Upshot. ChatGPT: done. */}
        <div className="su-v-page3 absolute inset-x-0 bottom-0 top-[26px] px-5 pt-4">
          <VendorLogo vendor={vendor} size={22} />
          {code ? (
            <>
              <p dir={dir} className="mt-2 text-[13px] leading-snug">{t(page.codeText)}</p>
              <div className="mt-3 rounded-md bg-[#f5f4ef] px-3 py-2 font-mono text-[11px]">8fJk2x9Lm…#Qw3eRt…</div>
              {button(t("firstRun.ai.scene.copy"), "su-v-press3")}
            </>
          ) : (
            <>
              <p dir={dir} className="mt-2 text-[13px] leading-snug">{t("firstRun.ai.scene.codexDone")}</p>
              <span className="mt-4 grid size-8 place-items-center rounded-full bg-[#10a37f] text-[14px] text-[#fff]">✓</span>
            </>
          )}
        </div>
      </div>

      <Pointer className={code ? "su-v-pointer-claude" : "su-v-pointer-codex"} />
    </div>
  );
}

/** Each vendor's sign-in pages, as the scene draws them: their words, domain and colour. */
const SCENE: Record<
  CliId,
  { name: MessageKey; domain: string; ink: string; login: MessageKey; allow: MessageKey; allowButton: MessageKey; codeText: MessageKey }
> = {
  claude: {
    name: "firstRun.ai.claude.name",
    domain: "claude.ai",
    ink: "#1f1e1d",
    login: "firstRun.ai.scene.claudeLogin",
    allow: "firstRun.ai.scene.claudeAllow",
    allowButton: "firstRun.ai.scene.authorize",
    codeText: "firstRun.ai.scene.claudeCode",
  },
  codex: {
    name: "firstRun.ai.codex.name",
    domain: "auth.openai.com",
    ink: "#0d0d0d",
    login: "firstRun.ai.scene.codexLogin",
    allow: "firstRun.ai.scene.codexAllow",
    allowButton: "firstRun.calendar.scene.continue",
    codeText: "firstRun.ai.scene.codexDone",
  },
  // Google's consent page, then antigravity.google's page with the code (machine B,
  // 2026-09-28).
  antigravity: {
    name: "firstRun.ai.antigravity.name",
    domain: "accounts.google.com",
    ink: "#1a73e8",
    login: "firstRun.ai.scene.googleLogin",
    allow: "firstRun.ai.scene.googleAllow",
    allowButton: "firstRun.calendar.scene.continue",
    codeText: "firstRun.ai.scene.googleCode",
  },
};

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
