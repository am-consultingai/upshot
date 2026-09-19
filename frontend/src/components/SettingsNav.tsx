import { useLocation, useNavigate } from "react-router-dom";
import { useI18n } from "../i18n";
import type { MessageKey } from "../locales/en";

export interface SettingsSection {
  id: string;
  label: MessageKey;
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
export default function SettingsNav({ sections }: { sections: SettingsSection[] }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { hash } = useLocation();
  const current = hash.replace("#", "") || sections[0].id;

  return (
    <nav data-testid="settings-nav" className="w-44 shrink-0" aria-label={t("nav.settings")}>
      <div className="sticky top-6 space-y-0.5">
        {sections.map((section) => {
          const active = section.id === current;
          return (
            <button
              key={section.id}
              type="button"
              data-testid={`settings-section-${section.id}`}
              aria-current={active ? "page" : undefined}
              onClick={() => navigate(`/settings#${section.id}`)}
              className={`block w-full rounded-md px-2.5 py-1.5 text-start text-sm transition-colors ${
                active
                  ? "bg-surface-2 font-medium text-primary"
                  : "text-secondary hover:bg-surface-1 hover:text-primary"
              }`}
            >
              {t(section.label)}
            </button>
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
