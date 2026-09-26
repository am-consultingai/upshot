import { useI18n } from "../../i18n";
import Tooltip from "../Tooltip";

/**
 * The assistant's way in: a round button in the lower corner of the window, over the
 * content, as a chat button is everywhere else. It opens and closes the floating
 * panel; Ctrl/Cmd+J and the command palette still do too. At the inline start, so in
 * Hebrew it sits in the lower right, mirroring the rest of the interface.
 */
export default function AssistantLauncher({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const { t } = useI18n();
  return (
    <div className="fixed bottom-5 start-5 z-40">
      <Tooltip label={t("assistant.open")} hint={t("help.assistant")} side="end">
        <button
          type="button"
          data-testid="assistant-launcher"
          aria-label={t("assistant.open")}
          aria-expanded={open}
          onClick={onToggle}
          className="grid size-14 place-items-center rounded-full bg-accent text-on-accent shadow-lg transition-transform hover:scale-105 hover:bg-accent-hover active:scale-95"
        >
          {open ? (
            <svg viewBox="0 0 24 24" className="size-6 fill-none stroke-current stroke-2" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="size-7 fill-none stroke-current" aria-hidden="true" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              {/* A robot: antenna, head, eyes, mouth, ears. */}
              <path d="M12 3.5v2.5" />
              <circle cx="12" cy="3" r="1" fill="currentColor" stroke="none" />
              <rect x="5" y="6.5" width="14" height="11" rx="3.5" />
              <circle cx="9.3" cy="11.5" r="1.3" fill="currentColor" stroke="none" />
              <circle cx="14.7" cy="11.5" r="1.3" fill="currentColor" stroke="none" />
              <path d="M9.5 14.8h5M3 10.5v3M21 10.5v3M8.5 17.5v2.5M15.5 17.5v2.5" />
            </svg>
          )}
        </button>
      </Tooltip>
    </div>
  );
}
