import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type ActionItem } from "../api";
import { useI18n } from "../i18n";
import ActionItemRow from "../components/ActionItemRow";

/**
 * Everything anyone took on, across every meeting.
 *
 * The screen this application was missing. A meeting-notes tool that files seven
 * well-written documents and cannot answer "what did I promise this week, and to
 * whom" is a filing cabinet; the person using it has a follow-through problem, not
 * a filing problem, and a notebook was beating us at it.
 *
 * Grouped by owner rather than by meeting, because the question is asked about a
 * person — mine first and always first, since it is the only group the reader can
 * actually act on.
 */
export default function ActionsPage() {
  const { t } = useI18n();
  const [openOnly, setOpenOnly] = useState(true);

  const query = useQuery({
    queryKey: ["action-items", openOnly ? "open" : "all"],
    queryFn: () => api.actionItems(openOnly ? { open: "true" } : {}),
  });

  const items = query.data?.items ?? [];
  const groups = groupByOwner(items);

  const toggle = (active: boolean) =>
    `rounded-sm px-2 py-0.5 text-2xs font-medium transition-colors ${
      active ? "bg-raised text-primary shadow-sm" : "text-secondary hover:text-primary"
    }`;

  return (
    <section data-testid="actions-page">
      <header className="mb-1 flex flex-wrap items-center gap-2">
        <h1 className="display text-2xl">{t("actions.title")}</h1>
        {query.data && query.data.open > 0 && (
          <span
            data-testid="actions-open-count"
            className="rounded-full bg-surface-3 px-2 py-0.5 text-2xs text-secondary tabular-nums"
          >
            {query.data.open} {t("actions.open")}
          </span>
        )}
        <div className="ms-auto flex gap-0.5 rounded-md bg-surface-3 p-0.5">
          <button
            type="button"
            data-testid="actions-open"
            aria-pressed={openOnly}
            onClick={() => setOpenOnly(true)}
            className={toggle(openOnly)}
          >
            {t("actions.showOpen")}
          </button>
          <button
            type="button"
            data-testid="actions-all"
            aria-pressed={!openOnly}
            onClick={() => setOpenOnly(false)}
            className={toggle(!openOnly)}
          >
            {t("actions.showAll")}
          </button>
        </div>
      </header>
      <p className="mb-6 text-sm text-tertiary">{t("actions.lead")}</p>

      {query.isLoading && <p className="text-sm text-tertiary">{t("common.loading")}</p>}
      {query.isError && <p className="text-sm text-danger">{t("common.error")}</p>}

      {!query.isLoading && !query.isError && items.length === 0 && (
        <p data-testid="actions-empty" className="py-10 text-center text-sm text-tertiary">
          {openOnly ? t("actions.emptyOpen") : t("actions.empty")}
        </p>
      )}

      {groups.map(([owner, rows]) => (
        <section key={owner} data-testid="action-group" data-owner={owner} className="mb-6">
          <h2 className="mb-1 px-2 text-xs font-medium text-tertiary">
            {rows[0].mine ? t("actions.mine") : owner}
            <span className="ms-1.5 tabular-nums opacity-60">{rows.length}</span>
          </h2>
          <ul>
            {rows.map((item) => (
              <ActionItemRow key={item.id} item={item} />
            ))}
          </ul>
        </section>
      ))}
    </section>
  );
}

/** The bucket every spelling of "me" collapses into; the server decides which those are. */
export const MINE = "\u0000me";

/**
 * Owner → their items, mine first and the rest alphabetical.
 *
 * Mine is sorted by an explicit test rather than by a sentinel key, because
 * `localeCompare` collates on letters and quietly ignores a leading control
 * character: "\u0000me" sorts exactly where "me" would, which put my own four items
 * between Maya's and Yoni's. The one group the reader can actually act on has to be
 * the one they see first.
 *
 * The server already returns items mine-first and newest-meeting-first, so within a
 * group the original order is kept rather than re-sorted.
 */
export function groupByOwner(items: ActionItem[]): [string, ActionItem[]][] {
  const byOwner = new Map<string, ActionItem[]>();
  for (const item of items) {
    const key = item.mine ? MINE : item.who;
    const bucket = byOwner.get(key);
    if (bucket) bucket.push(item);
    else byOwner.set(key, [item]);
  }
  return [...byOwner.entries()].sort(([a], [b]) => {
    if (a === MINE) return -1;
    if (b === MINE) return 1;
    return a.localeCompare(b);
  });
}
