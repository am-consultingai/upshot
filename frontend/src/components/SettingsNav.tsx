import { useLocation, useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";
import SetupMark from "./SetupMark";
import Tooltip from "./Tooltip";

export interface SettingsSection {
  id: string;
  label: MessageKey;
  /** What the section is for, on hover — for the ones a name does not explain. */
  hint?: MessageKey;
}

/**
 * The section list for Settings, and the reason it exists is not decoration.
 *
 * As one long page, every section rendered above whatever you were looking at,
 * and the provider section grows after load — its status query resolves, its
 * key rows and sign-in buttons appear — so arriving at the prompt from a
 * meeting's "View prompt" scrolled you there and then pushed you off it as the
 * section above finished rendering. A section is its own screen here, so there
 * is nothing above it to move.
 *
 * Microsoft's guidance prefers one scrolling column and no second navigation,
 * and that holds while a page has four or five groups. This has five plus two
 * long ones, and the two long ones are the unstable ones.
 *
 * The section lives in the URL hash, so "View prompt" keeps working as a link
 * and the browser's back button still means something.
 */
export default function SettingsNav({
  sections,
  warnings = {},
}: {
  sections: SettingsSection[];
  /** Section id → why it needs attention; those sections carry a "!". */
  warnings?: Record<string, string>;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const current = hash.replace("#", "") || sections[0].id;

  return (
    <nav data-testid="settings-nav" className="w-44 shrink-0" aria-label={t("nav.settings")}>
      <div className="sticky top-6 space-y-0.5">
        {sections.map((section) => {
          const active = section.id === current;
          const button = (
            <button
              key={section.id}
              type="button"
              data-testid={`settings-section-${section.id}`}
              aria-current={active ? "page" : undefined}
              onClick={() => navigate(`/settings#${section.id}`)}
              className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-start text-sm transition-colors ${
                active
                  ? "bg-surface-2 font-medium text-primary"
                  : "text-secondary hover:bg-a-200 hover:text-primary active:bg-a-300"
              }`}
            >
              <span className="min-w-0 flex-1 truncate">{t(section.label)}</span>
              {warnings[section.id] && (
                <SetupMark testid={`settings-warning-${section.id}`} label={warnings[section.id]} />
              )}
            </button>
          );
          return section.hint ? (
            <Tooltip key={section.id} label={t(section.label)} hint={t(section.hint)} side="end">
              {button}
            </Tooltip>
          ) : (
            button
          );
        })}
      </div>
    </nav>
  );
}

/** Which section is showing, from the hash, defaulting to the first. */
export function useSection(sections: SettingsSection[]): string {
  const { hash } = useLocation();
  const id = hash.replace("#", "");
  return sections.some((section) => section.id === id) ? id : sections[0].id;
}
