import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useI18n } from "../i18n";
import { fill } from "../setup/ui";
import Button from "../components/Button";
import { Loading, Skeleton, SkeletonProse } from "../components/Skeleton";

/**
 * The Terms of Service (D83).
 *
 * While `gate` is set (no version accepted yet, or a material change in effect) the app
 * routes here instead of anywhere else, and the only ways on are "I agree" and "Decline
 * and quit". Otherwise this is a page to read them: the version in effect, or the one
 * named by `?version=` (an update announced before it applies).
 *
 * The text is the backend's rendering of `app/legal/terms.md`, the same file the
 * installer and the website show, so what is agreed to here is word for word the same.
 */
export default function TermsPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const [agreed, setAgreed] = useState(false);
  const [declined, setDeclined] = useState(false);

  const legal = useQuery({ queryKey: ["legal"], queryFn: api.legal });
  const gate = legal.data?.gate === true;
  const version = params.get("version") ?? undefined;
  const doc = useQuery({
    queryKey: ["terms", gate ? "current" : (version ?? "current")],
    queryFn: () => api.terms(gate ? undefined : version),
    enabled: legal.isSuccess,
  });

  const accept = useMutation({
    mutationFn: (v: string) => api.acceptTerms(v),
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      navigate("/", { replace: true });
    },
  });
  const decline = useMutation({
    mutationFn: api.declineTerms,
    onSuccess: (result) => setDeclined(!result.quitting),
  });

  if (legal.isError || doc.isError) {
    return <p className="p-8 text-sm text-danger">{t("terms.error")}</p>;
  }
  if (!legal.data || !doc.data)
    return (
      <Loading className="mx-auto w-full max-w-3xl px-8 pt-8">
        <Skeleton className="mb-3 h-7 w-1/2 rounded-md" />
        <Skeleton className="mb-8 h-3 w-3/4" />
        <SkeletonProse lines={6} className="mb-6" />
        <SkeletonProse lines={5} />
      </Loading>
    );

  const updated = gate && legal.data.accepted_version !== null;
  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden" data-testid="terms">
      <div className="mx-auto flex w-full max-w-3xl min-h-0 flex-1 flex-col px-8 pt-8">
        <h1 className="display text-2xl">{updated ? t("terms.updatedTitle") : t("terms.title")}</h1>
        <p className="mt-2 text-sm text-secondary">
          {gate
            ? updated
              ? t("terms.updatedLead")
              : t("terms.firstLead")
            : legal.data.accepted_version
              ? fill(t("terms.acceptedOn"), {
                  version: legal.data.accepted_version,
                  date: (legal.data.accepted_at ?? "").slice(0, 10),
                })
              : ""}
        </p>
        {updated && doc.data.summary && (
          <p className="mt-3 rounded-md bg-surface-2 px-4 py-3 text-sm" data-testid="terms-summary">
            {doc.data.summary}
          </p>
        )}
        <p className="mt-3 text-xs text-tertiary">
          {fill(t("terms.versionLine"), { version: doc.data.version, effective: doc.data.effective })}
        </p>
        <article
          className="terms-text mt-3 min-h-0 flex-1 overflow-y-auto rounded-md border border-line px-5 py-4 text-sm leading-relaxed"
          data-testid="terms-text"
          // Rendered and escaped by app/legal/document.py from the bundled or checksummed file.
          dangerouslySetInnerHTML={{ __html: doc.data.html }}
        />
        {gate ? (
          <div className="flex flex-col gap-3 py-5">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={agreed}
                onChange={(event) => setAgreed(event.target.checked)}
                data-testid="terms-agree"
              />
              <span>
                {t("terms.agreeBefore")}
                <a href={legal.data.page} target="_blank" rel="noreferrer" className="underline">
                  {t("terms.termsLink")}
                </a>
                {t("terms.agreeMiddle")}
                <a
                  href="https://upshot.amconsultingai.com/privacy.html"
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  {t("terms.privacyLink")}
                </a>
                .
              </span>
            </label>
            <div className="flex flex-row-reverse items-center justify-between gap-2">
              <Button
                variant="primary"
                size="md"
                disabled={!agreed || accept.isPending}
                onClick={() => accept.mutate(doc.data.version)}
                data-testid="terms-accept"
              >
                {t("terms.accept")}
              </Button>
              <Button
                variant="ghost"
                size="md"
                disabled={decline.isPending}
                onClick={() => decline.mutate()}
                data-testid="terms-decline"
              >
                {t("terms.decline")}
              </Button>
            </div>
            {declined && <p className="text-sm text-secondary">{t("terms.declinedNoQuit")}</p>}
            {accept.isError && <p className="text-sm text-danger">{t("terms.error")}</p>}
          </div>
        ) : (
          <div className="py-5">
            <Button variant="ghost" size="md" onClick={() => navigate(-1)}>
              {t("terms.back")}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
