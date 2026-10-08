import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, reason } from "../api";
import { useI18n } from "../i18n";
import Button from "./Button";

/**
 * Was this summary good? (D87, D3). Up or down, then an optional comment. What is sent:
 * the rating, the comment, and which provider and prompt wrote the summary. The summary
 * itself only if the user ticks it, under a warning that it is meeting content. The
 * choice is kept with the meeting, so it shows here next time.
 */
export default function SummaryRating({ meetingId }: { meetingId: string }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const kept = useQuery({ queryKey: ["rating", meetingId], queryFn: () => api.summaryRating(meetingId) });
  const [choice, setChoice] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [include, setInclude] = useState(false);
  const [thanks, setThanks] = useState<string | null>(null);
  const rate = useMutation({
    mutationFn: () => api.rateSummary(meetingId, { rating: choice!, comment, include_summary: include }),
    onSuccess: (result) => {
      setThanks(result.reference ? t("rating.thanksRef").replace("{reference}", result.reference) : t("rating.thanks"));
      setChoice(null);
      void queryClient.invalidateQueries({ queryKey: ["rating", meetingId] });
    },
  });
  const current = kept.data?.rating?.value ?? null;

  return (
    <section data-testid="summary-rating" className="mb-10 rounded-lg bg-surface-2 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-secondary">{t("rating.question")}</span>
        {(["up", "down"] as const).map((value) => (
          <button
            key={value}
            type="button"
            data-testid={`rating-${value}`}
            aria-pressed={(choice ?? current) === value}
            aria-label={t(value === "up" ? "rating.up" : "rating.down")}
            onClick={() => {
              setThanks(null);
              setChoice(value);
            }}
            className={`rounded-md px-2 py-1 text-base ${(choice ?? current) === value ? "bg-accent text-on-accent" : "hover:bg-a-200"}`}
          >
            {value === "up" ? "👍" : "👎"}
          </button>
        ))}
        {thanks && (
          <span data-testid="rating-thanks" className="text-secondary">
            {thanks}
          </span>
        )}
      </div>
      {choice && (
        <div className="mt-3">
          <textarea
            data-testid="rating-comment"
            aria-label={t("rating.comment")}
            placeholder={t("rating.comment")}
            value={comment}
            maxLength={5000}
            onChange={(event) => setComment(event.target.value)}
            className="mb-2 h-20 w-full rounded border border-line bg-canvas p-2 text-sm"
          />
          <label className="mb-2 flex items-start gap-2">
            <input
              type="checkbox"
              data-testid="rating-include"
              checked={include}
              onChange={(event) => setInclude(event.target.checked)}
              className="mt-0.5 size-4 accent-[var(--accent)]"
            />
            <span>
              {t("rating.include")}
              <span className="block text-xs text-tertiary">{t("rating.includeHint")}</span>
            </span>
          </label>
          {rate.isError && <p className="mb-2 text-danger">{reason(rate.error)}</p>}
          <div className="flex gap-2">
            <Button
              data-testid="rating-send"
              disabled={rate.isPending}
              onClick={() => rate.mutate()}
              variant="primary"
            >
              {t("rating.send")}
            </Button>
            <Button variant="ghost" onClick={() => setChoice(null)}>
              {t("feedback.cancel")}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
