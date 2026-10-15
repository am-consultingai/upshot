import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type NoticeEntry } from "../api";
import { useI18n } from "../i18n";
import { fill } from "../setup/ui";
import { Loading, Skeleton } from "../components/Skeleton";

/**
 * Open-source licences: every component the app ships or the installer fetches, with its
 * licence (licence epic, S8). The list is the build's THIRD-PARTY-NOTICES.json, made from
 * the freeze itself; each entry's texts load when it is opened.
 */

/**
 * Credits FreeType's and the IJG's licences ask for in the documentation (both inside
 * Pillow). They are legal wording, so they stay in English in every language.
 */
const CREDITS = [
  "Portions of this software are copyright © The FreeType Project (www.freetype.org). All rights reserved.",
  "This software is based in part on the work of the Independent JPEG Group.",
];

export default function LicencesPage() {
  const { t } = useI18n();
  const [filter, setFilter] = useState("");
  const list = useQuery({
    queryKey: ["notices"],
    queryFn: api.notices,
    staleTime: Infinity,
  });

  if (list.isError)
    return <p className="p-8 text-sm text-danger">{t("licences.error")}</p>;
  const needle = filter.trim().toLowerCase();
  const shown = (list.data?.entries ?? []).filter(
    (entry) =>
      !needle ||
      `${entry.name} ${entry.licence}`.toLowerCase().includes(needle),
  );

  return (
    <div className="mx-auto w-full max-w-3xl px-8 py-8" data-testid="licences">
      <Link to="/settings#about" className="text-sm text-secondary underline">
        {t("licences.back")}
      </Link>
      <h1 className="display mt-3 text-2xl">{t("licences.title")}</h1>
      <p className="mt-2 text-sm text-secondary">{t("licences.lead")}</p>
      <div
        className="mt-3 space-y-1 text-xs text-tertiary"
        dir="ltr"
        data-testid="licences-credits"
      >
        {CREDITS.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
      {!list.data ? (
        <Loading className="mt-6">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="mb-3 h-5 w-2/3" />
          ))}
        </Loading>
      ) : !list.data.available ? (
        <p
          className="mt-6 text-sm text-secondary"
          data-testid="licences-missing"
        >
          {t("licences.missing")}
        </p>
      ) : (
        <>
          <input
            type="search"
            dir="auto"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder={t("licences.search")}
            aria-label={t("licences.search")}
            data-testid="licences-search"
            className="mt-6 w-full rounded-md border border-line-subtle bg-raised px-2 py-1 text-sm outline-none focus:border-accent"
          />
          <p className="mt-2 text-xs text-tertiary">
            {fill(t("licences.count"), { count: String(shown.length) })}
          </p>
          <ul
            className="mt-2 divide-y divide-line-subtle border-y border-line-subtle"
            dir="ltr"
          >
            {shown.map((entry) => (
              <NoticeRow key={entry.id} entry={entry} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function NoticeRow({ entry }: { entry: NoticeEntry }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const texts = useQuery({
    queryKey: ["notices", entry.id],
    queryFn: () => api.noticeTexts(entry.id),
    enabled: open,
    staleTime: Infinity,
  });
  return (
    <li data-testid="licence-entry">
      <details onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="flex cursor-pointer items-baseline gap-2 py-2 text-sm">
          <span className="font-medium">{entry.name}</span>
          {entry.version && (
            <span className="text-tertiary">{entry.version}</span>
          )}
          <span
            className="ms-auto shrink-0 text-xs text-secondary"
            data-testid="licence-id"
          >
            {entry.licence}
          </span>
        </summary>
        <div className="pb-3 text-xs">
          {entry.note && <p className="text-secondary">{entry.note}</p>}
          {entry.source && (
            <p className="mt-1 break-words text-secondary">
              {t("licences.source")} {entry.source}
            </p>
          )}
          {texts.isError && (
            <p className="mt-2 text-danger">{t("licences.error")}</p>
          )}
          {!texts.data && !texts.isError && (
            <Skeleton className="mt-2 h-24 w-full" />
          )}
          {texts.data?.texts.map((text, index) => (
            <pre
              key={index}
              data-testid="licence-text"
              className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 p-3 font-mono text-[11px] leading-snug"
            >
              {text}
            </pre>
          ))}
        </div>
      </details>
    </li>
  );
}
