import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useI18n } from "../i18n";
import { fill } from "../setup/ui";
import Banner from "./Banner";
import Button from "./Button";

const DISMISSED = "upshot.terms.dismissed";

/**
 * An update to the Terms that does not need the whole screen (D83): a change that is
 * not material, which "OK" accepts, or one announced before it takes effect, which can
 * be read now and dismissed until it applies. A material change in effect is the Terms
 * screen's job, not this banner's.
 */
export default function TermsNotice() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const legal = useQuery({ queryKey: ["legal"], queryFn: api.legal, refetchInterval: 60 * 60 * 1000 });
  const [dismissed, setDismissed] = useState<string | null>(() => readDismissed());
  const accept = useMutation({
    mutationFn: (version: string) => api.acceptTerms(version),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["legal"] }),
  });

  const notice = legal.data?.gate ? null : legal.data?.notice;
  if (!notice) return null;
  const key = `${notice.kind}:${notice.version}`;
  if (notice.kind === "upcoming" && dismissed === key) return null;

  const read = () =>
    navigate(notice.kind === "upcoming" ? `/terms?version=${encodeURIComponent(notice.version)}` : "/terms");
  return (
    <Banner
      data-testid="terms-notice"
      role="status"
      tone="notice"
      actions={
        notice.kind === "changed" ? (
          <Button
            variant="ghost"
            disabled={accept.isPending}
            onClick={() => accept.mutate(notice.version)}
            data-testid="terms-notice-ok"
          >
            {t("terms.ok")}
          </Button>
        ) : (
          <Button
            variant="ghost"
            onClick={() => {
              writeDismissed(key);
              setDismissed(key);
            }}
            aria-label={t("terms.dismiss")}
          >
            ×
          </Button>
        )
      }
    >
      <span>
        {fill(t(notice.kind === "upcoming" ? "terms.noticeUpcoming" : "terms.noticeChanged"), {
          date: notice.effective,
        })}
      </span>
      <button type="button" className="underline" onClick={read} data-testid="terms-notice-read">
        {t("terms.read")}
      </button>
    </Banner>
  );
}

function readDismissed(): string | null {
  try {
    return window.localStorage.getItem(DISMISSED);
  } catch {
    return null;
  }
}

function writeDismissed(value: string): void {
  try {
    window.localStorage.setItem(DISMISSED, value);
  } catch {
    // A private window: the banner comes back next time, which is harmless.
  }
}
