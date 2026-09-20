import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";

/**
 * The one question the app must not answer for you in silence.
 *
 * `detection.mode` defaults to `shadow`: the detector watches, scores and logs, and
 * never starts a recording. That is the right *privacy* default and it stays — but
 * as a silent default it means a new install captures nothing at all until the user
 * happens to find the red button, and someone who joins a call three minutes late
 * with their camera already on does not find the red button. They find out at six in
 * the evening, when there are no notes.
 *
 * So the default is kept and the silence is removed: until `detection.decided` is
 * true, the library asks. Answering either way is what sets it, so this appears once
 * per install and then never again.
 */
export default function CaptureChoice() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

  const choose = useMutation({
    mutationFn: (mode: "on" | "shadow") =>
      api.putSettings({ "detection.mode": mode, "detection.decided": true }),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  // Undefined while the first status is in flight: asking and then withdrawing the
  // question a moment later is worse than asking a moment late.
  if (status.data?.detector.decided !== false) return null;

  const option =
    "flex-1 rounded-lg border border-line bg-surface-1 p-3 text-start transition-colors hover:border-accent disabled:opacity-50";

  return (
    <section
      data-testid="capture-choice"
      className="mx-4 mt-4 rounded-xl border border-line bg-raised p-5 shadow-md"
    >
      <h2 className="display mb-1 text-lg">{t("capture.heading")}</h2>
      <p className="mb-2 text-sm text-secondary">{t("capture.lead")}</p>
      <p className="mb-4 text-sm text-tertiary">{t("capture.privacy")}</p>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="capture-automatic"
          disabled={choose.isPending}
          onClick={() => choose.mutate("on")}
          className={option}
        >
          <span className="block text-sm font-medium text-primary">{t("capture.automatic")}</span>
          <span className="mt-0.5 block text-xs text-tertiary">{t("capture.automaticWhy")}</span>
        </button>
        <button
          type="button"
          data-testid="capture-manual"
          disabled={choose.isPending}
          onClick={() => choose.mutate("shadow")}
          className={option}
        >
          <span className="block text-sm font-medium text-primary">{t("capture.manual")}</span>
          <span className="mt-0.5 block text-xs text-tertiary">{t("capture.manualWhy")}</span>
        </button>
      </div>
      <p className="mt-3 text-2xs text-tertiary">{t("capture.changeLater")}</p>
    </section>
  );
}
