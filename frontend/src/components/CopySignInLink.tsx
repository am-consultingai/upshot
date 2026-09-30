import { useState } from "react";
import { useI18n } from "../i18n";

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
