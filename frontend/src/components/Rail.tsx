import { NavLink } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { Logo } from "./Logo";
import type { MessageKey } from "../locales/en";

/**
 * The icon rail: 56px of permanent navigation down the side.
 *
 * It replaces a horizontal bar of text links across the top. The bar cost a full
 * row of vertical space on every screen and still had nowhere to put a persistent
 * search or a record button; the rail costs 48px and puts both within reach from
 * anywhere.
 *
 * 48px rather than the 56 this started at: Superhuman's rail is exactly 40px and
 * Obsidian's ribbon is 44px, so 56 was wider than anything actually shipped.
 *
 * Icons are drawn on a 16px grid at stroke 1.5, which is what the products worth
 * copying actually ship. Scaling a 24px/2.0 icon down to 16px puts its stroke on
 * half-pixel boundaries and blurs it.
 */
const ITEMS: { to: string; key: MessageKey; testid: string; path: string }[] = [
  {
    to: "/",
    key: "nav.timeline",
    testid: "nav-timeline",
    // A calendar page with its two rings. The three stacked lines this used to be
    // promised a list, and the screen behind it is a calendar.
    path: "M2.5 4.5h11v9h-11zM2.5 7h11M5.5 2.5v2M10.5 2.5v2",
  },
  {
    to: "/actions",
    key: "nav.actions",
    testid: "nav-actions",
    // A ticked box: the one screen in the app you act on rather than read.
    path: "M3 8.5 6.2 11.6 13 4.8",
  },
  {
    to: "/search",
    key: "nav.search",
    testid: "nav-search",
    path: "M10.5 10.5 14 14M11.5 7a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z",
  },
  {
    to: "/settings",
    key: "nav.settings",
    testid: "nav-settings",
    path: "M8 10.2A2.2 2.2 0 1 0 8 5.8a2.2 2.2 0 0 0 0 4.4ZM8 1.8v1.4M8 12.8v1.4M14.2 8h-1.4M3.2 8H1.8M12.4 3.6l-1 1M4.6 11.4l-1 1M12.4 12.4l-1-1M4.6 4.6l-1-1",
  },
];

export default function Rail() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const start = useMutation({
    mutationFn: api.startRecording,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const recording = status.data?.recorder.active ?? false;

  return (
    <nav
      data-testid="rail"
      className="flex w-12 shrink-0 flex-col items-center gap-1.5 border-e border-line-subtle bg-surface-2 py-3"
    >
      <span
        data-testid="app-title"
        title={t("app.title")}
        className="mb-2.5 grid size-6.5 place-items-center rounded-lg bg-accent text-on-accent"
      >
        <Logo className="w-4.5" />
      </span>

      {/*
       * Recording sits at the top, not the foot.
       *
       * Apple's guidance is blunt about the reason: "avoid putting critical
       * information or actions at the bottom of a sidebar. People often relocate a
       * window in a way that hides its bottom edge." A settings icon down there is
       * survivable; the control that starts and stops a recording is not.
       */}
      <button
        type="button"
        data-testid="start-recording"
        disabled={recording || start.isPending}
        onClick={() => start.mutate()}
        title={t("timeline.start")}
        aria-label={t("timeline.start")}
        className="mb-2 grid size-8.5 place-items-center rounded-full bg-danger hover:brightness-110 active:translate-y-px active:brightness-95 disabled:opacity-40 disabled:hover:brightness-100"
      >
        <span className="size-2.5 rounded-full bg-on-solid" />
      </button>

      {ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          data-testid={item.testid}
          title={t(item.key)}
          aria-label={t(item.key)}
          className={({ isActive }) =>
            `grid size-8.5 place-items-center rounded-lg transition-colors ${
              isActive
                ? "bg-raised text-primary shadow-sm"
                : "text-tertiary hover:bg-a-200 hover:text-primary active:bg-a-300"
            }`
          }
        >
          <svg viewBox="0 0 16 16" className="size-4.5 fill-none stroke-current stroke-[1.5]">
            <path d={item.path} />
          </svg>
        </NavLink>
      ))}


    </nav>
  );
}
