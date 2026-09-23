import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ActionItem } from "../api";
import { useI18n } from "../i18n";
import ActionItemRow from "./ActionItemRow";
import Tooltip from "./Tooltip";

const COLLAPSED_KEY = "ma.meeting.actionsCollapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * The commitments this meeting recorded, above the summary.
 *
 * A framed block opening with how many are done — "1 of 3 done", in words, because
 * "1 / 3" read as a fraction or a page number. Circleback opens every note with
 * exactly this and it is the only part of the page with a verb in it.
 *
 * "+ Add" writes a commitment the summary missed. It is stored as the user's, so a
 * re-summarize — which rewrites every row the model wrote — keeps it. The chevron
 * folds the block for someone who reads a meeting for the discussion; that choice is
 * remembered per browser, as a convenience.
 */
export default function ActionItemsBlock({
  meetingId,
  items,
  addRequest,
}: {
  meetingId: string;
  items: ActionItem[];
  /** Bumped from outside (a "next action" in the summary) to open the add row pre-filled. */
  addRequest?: { text: string; at: number } | null;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [seen, setSeen] = useState<number | null>(null);

  if (addRequest && addRequest.at !== seen) {
    setSeen(addRequest.at);
    setAdding(true);
    setCollapsed(false);
    setDraft(addRequest.text);
  }

  const add = useMutation({
    mutationFn: (what: string) => api.addActionItem(meetingId, { what, who: "ME" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
      void queryClient.invalidateQueries({ queryKey: ["action-items"] });
      void queryClient.invalidateQueries({ queryKey: ["meetings"] });
    },
  });

  const toggleCollapsed = () => {
    setCollapsed((was) => {
      try {
        window.localStorage.setItem(COLLAPSED_KEY, was ? "0" : "1");
      } catch {
        /* a convenience only */
      }
      return !was;
    });
  };

  const done = items.filter((item) => item.done).length;
  const submit = () => {
    const what = draft.trim();
    if (what) add.mutate(what);
    setDraft("");
    setAdding(false);
  };

  return (
    <section
      data-testid="meeting-actions"
      data-collapsed={collapsed ? "true" : undefined}
      className="mb-7 rounded-xl bg-surface-1 px-1.5 pt-2.5 pb-1.5 shadow-[var(--shadow-ring-subtle)]"
    >
      <h2 className="mb-1 flex items-center gap-2 px-2 text-sm font-semibold tracking-snug">
        {t("meeting.actionItems")}
        {items.length > 0 && (
          <span data-testid="meeting-actions-progress" className="font-mono text-2xs font-normal text-tertiary tabular-nums">
            {t("meeting.doneOf").replace("{done}", String(done)).replace("{total}", String(items.length))}
          </span>
        )}
        <Tooltip label={t("meeting.addAction")} hint={t("help.addAction")}>
        <button
          type="button"
          data-testid="meeting-actions-add"
          onClick={() => {
            setCollapsed(false);
            setAdding(true);
          }}
          className="ms-auto h-6 rounded-sm px-1.5 text-xs font-normal text-tertiary hover:bg-a-200 hover:text-primary"
        >
          + {t("meeting.addAction")}
        </button>
        </Tooltip>
        <button
          type="button"
          data-testid="meeting-actions-collapse"
          aria-expanded={!collapsed}
          aria-label={collapsed ? t("meeting.expand") : t("meeting.collapse")}
          title={collapsed ? t("meeting.expand") : t("meeting.collapse")}
          onClick={toggleCollapsed}
          className="grid size-6 place-items-center rounded-sm text-tertiary hover:bg-a-200 hover:text-primary"
        >
          <svg
            viewBox="0 0 16 16"
            className={`size-3 fill-none stroke-current stroke-[1.6] transition-transform ${collapsed ? "-rotate-90 rtl:rotate-90" : ""}`}
          >
            <path d="M4 6.5 8 10.5l4-4" />
          </svg>
        </button>
      </h2>
      {!collapsed && (
        <ul className="divide-y divide-line-subtle">
          {items.map((item) => (
            <ActionItemRow key={item.id} item={item} showMeeting={false} />
          ))}
          {items.length === 0 && !adding && (
            <li className="px-2.5 py-2 text-sm text-tertiary">{t("meeting.noActionItems")}</li>
          )}
          {adding && (
            <li className="flex items-center gap-2.5 px-2.5 py-2">
              <span aria-hidden="true" className="size-[18px] shrink-0 rounded-full shadow-[inset_0_0_0_1.5px_var(--border-strong)]" />
              <input
                data-testid="meeting-actions-input"
                autoFocus
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={t("meeting.addActionPlaceholder")}
                onBlur={submit}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    submit();
                  }
                  if (event.key === "Escape") {
                    setDraft("");
                    setAdding(false);
                  }
                }}
                className="no-focus-ring min-w-0 flex-1 bg-transparent text-md outline-none placeholder:text-tertiary"
              />
            </li>
          )}
        </ul>
      )}
    </section>
  );
}
