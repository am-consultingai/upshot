import { useI18n } from "../../i18n";
import Tooltip from "../Tooltip";
import { Bot, X } from "lucide-react";
import { Icon } from "../Icon";

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
            <Icon icon={X} className="size-6" />
          ) : (
            <Icon icon={Bot} className="size-7" />
          )}
        </button>
      </Tooltip>
    </div>
  );
}
