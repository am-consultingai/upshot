import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type BrowserProfile } from "../api";
import { useI18n } from "../i18n";
import { SELECT_CLASS } from "./SettingRow";

/**
 * Which browser profile Google's sign-in opens in (ClickUp z8tj1hca86).
 *
 * Upshot's window runs in one profile of the default browser, and a page it opens lands
 * there whatever window has the focus. With more than one profile (Chrome and Edge list
 * theirs) the user picks; Upshot then opens the page in that profile itself. One profile,
 * or another browser: nothing to choose, and the page opens the link as before.
 */
export function useBrowserProfile(): {
  profiles: BrowserProfile[];
  choice: string;
  setChoice: (id: string) => void;
} {
  const query = useQuery({ queryKey: ["browser-profiles"], queryFn: api.calendarProfiles, staleTime: 60_000 });
  const profiles = query.data?.profiles ?? [];
  const [choice, setChoice] = useState("");
  useEffect(() => {
    if (choice || profiles.length < 2) return;
    const last = query.data?.last ?? "";
    setChoice(profiles.some((p) => p.id === last) ? last : profiles[0].id);
  }, [choice, profiles, query.data?.last]);
  return { profiles: profiles.length > 1 ? profiles : [], choice, setChoice };
}

export function profileLabel(profile: BrowserProfile): string {
  return profile.email && profile.email !== profile.name ? `${profile.name} (${profile.email})` : profile.name;
}

export default function BrowserProfilePicker({
  profiles,
  choice,
  onChange,
}: {
  profiles: BrowserProfile[];
  choice: string;
  onChange: (id: string) => void;
}) {
  const { t } = useI18n();
  if (profiles.length < 2) return null;
  return (
    <label className="flex flex-wrap items-center gap-2 text-sm text-secondary">
      <span>{t("calendar.openIn")}</span>
      <select
        data-testid="calendar-profile"
        value={choice}
        onChange={(event) => onChange(event.target.value)}
        className={SELECT_CLASS}
      >
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profileLabel(profile)}
          </option>
        ))}
      </select>
    </label>
  );
}

/** "Copy sign-in link": works for any browser, and any window of it. */
export function CopySignInLink({ url }: { url: string | null | undefined }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  if (!url) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        data-testid="calendar-copy-link"
        className="underline"
        onClick={() => {
          void navigator.clipboard?.writeText(url).then(() => setCopied(true));
        }}
      >
        {t("calendar.copyLink")}
      </button>
      {copied && <span data-testid="calendar-copied">{t("calendar.copied")}</span>}
    </span>
  );
}
