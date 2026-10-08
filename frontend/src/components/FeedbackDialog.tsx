import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, reason, type FeedbackBody } from "../api";
import { useI18n, type MessageKey } from "../i18n";
import Button from "./Button";

const KINDS: { value: FeedbackBody["kind"]; label: MessageKey }[] = [
  { value: "idea", label: "feedback.kind.idea" },
  { value: "problem", label: "feedback.kind.problem" },
  { value: "praise", label: "feedback.kind.praise" },
  { value: "other", label: "feedback.kind.other" },
];

/** Open the feedback dialog from anywhere: the palette, Settings, the tray. */
export function openFeedback(): void {
  window.dispatchEvent(new CustomEvent("upshot:feedback"));
}

/**
 * Feedback from inside the app (D87, D1-D2). Anonymous unless an email is added; the
 * user's words go as written; "See exactly what will be sent" shows the payload; a
 * screenshot is of Upshot's own window, taken with this dialog out of the way, shown
 * before it is sent and removable. The tray opens it with `?feedback=1`.
 */
export default function FeedbackDialog() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [kind, setKind] = useState<FeedbackBody["kind"]>("idea");
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [details, setDetails] = useState(true);
  const [shot, setShot] = useState<string | null>(null);
  const [shotFailed, setShotFailed] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ reference: string; sent: boolean } | null>(null);
  const state = useQuery({ queryKey: ["feedback"], queryFn: api.feedback, enabled: open });

  const reset = useCallback(() => {
    setKind("idea");
    setMessage("");
    setEmail("");
    setDetails(true);
    setShot(null);
    setShotFailed(false);
    setPreview(null);
    setError(null);
    setDone(null);
  }, []);

  useEffect(() => {
    const show = () => {
      reset();
      setOpen(true);
    };
    window.addEventListener("upshot:feedback", show);
    // The tray's "Send feedback…" opens the window with ?feedback=1.
    const params = new URLSearchParams(window.location.search);
    if (params.get("feedback") === "1") {
      params.delete("feedback");
      const rest = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : ""));
      show();
    }
    return () => window.removeEventListener("upshot:feedback", show);
  }, [reset]);

  const body = (): FeedbackBody => ({ kind, message, email, details, screenshot: !!shot });

  const attachScreenshot = async (on: boolean) => {
    setShotFailed(false);
    if (!on) {
      setShot(null);
      void api.dropScreenshot().catch(() => undefined);
      return;
    }
    // Out of the way first, so the picture is of Upshot, not of this dialog.
    setHidden(true);
    await new Promise((resolve) => window.setTimeout(resolve, 350));
    try {
      setShot(await api.takeScreenshot());
    } catch {
      setShotFailed(true);
    } finally {
      setHidden(false);
    }
  };

  const showPreview = async () => {
    setError(null);
    try {
      setPreview(JSON.stringify(await api.feedbackPreview(body()), null, 2));
    } catch (exc) {
      setError(reason(exc));
    }
  };

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      setDone(await api.sendFeedback(body()));
    } catch (exc) {
      setError(reason(exc));
    } finally {
      setBusy(false);
    }
  };

  if (!open || hidden) return null;
  const available = state.data?.available ?? true;
  return (
    <div className="fixed inset-0 z-[80] grid place-items-center bg-scrim-soft px-4 backdrop-blur-xs" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="feedback-title"
        data-testid="feedback-dialog"
        className="max-h-[90vh] w-full max-w-lg overflow-auto rounded-xl bg-raised p-5 shadow-lg"
      >
        <h2 id="feedback-title" className="display mb-1 text-xl">
          {t("feedback.title")}
        </h2>
        {done ? (
          <>
            <p data-testid="feedback-done" className="my-4 text-sm">
              {t(done.sent ? "feedback.sent" : "feedback.kept").replace("{reference}", done.reference)}
            </p>
            <div className="flex justify-end">
              <Button variant="primary" onClick={() => setOpen(false)}>
                {t("feedback.close")}
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="mb-4 text-sm text-secondary">{t("feedback.lead")}</p>
            {!available && (
              <p data-testid="feedback-unavailable" className="mb-3 text-sm text-danger">
                {t("feedback.unavailable")}
              </p>
            )}
            <div role="radiogroup" aria-label={t("feedback.kindLabel")} className="mb-3 flex flex-wrap gap-2">
              {KINDS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={kind === option.value}
                  data-testid={`feedback-kind-${option.value}`}
                  onClick={() => setKind(option.value)}
                  className={`rounded-full px-3 py-1 text-sm ${kind === option.value ? "bg-accent text-on-accent" : "bg-surface-2"}`}
                >
                  {t(option.label)}
                </button>
              ))}
            </div>
            <textarea
              data-testid="feedback-message"
              aria-label={t("feedback.messageLabel")}
              placeholder={t("feedback.messagePlaceholder")}
              value={message}
              maxLength={5000}
              onChange={(event) => setMessage(event.target.value)}
              className="mb-3 h-32 w-full rounded border border-line bg-canvas p-2 text-sm"
            />
            <label className="mb-1 block text-xs text-secondary" htmlFor="feedback-email">
              {t("feedback.emailLabel")}
            </label>
            <input
              id="feedback-email"
              data-testid="feedback-email"
              type="email"
              dir="ltr"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="mb-3 w-full rounded border border-line bg-canvas p-2 text-sm"
            />
            <label className="mb-2 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="feedback-details"
                checked={details}
                onChange={(event) => setDetails(event.target.checked)}
                className="mt-0.5 size-4 accent-[var(--accent)]"
              />
              <span>
                {t("feedback.details")}
                <span className="block text-xs text-tertiary">{t("feedback.detailsHint")}</span>
              </span>
            </label>
            <label className="mb-2 flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="feedback-screenshot"
                checked={!!shot}
                onChange={(event) => void attachScreenshot(event.target.checked)}
                className="mt-0.5 size-4 accent-[var(--accent)]"
              />
              <span>
                {t("feedback.screenshot")}
                <span className="block text-xs text-tertiary">{t("feedback.screenshotHint")}</span>
              </span>
            </label>
            {shotFailed && <p className="mb-2 text-xs text-danger">{t("feedback.screenshotFailed")}</p>}
            {shot && (
              <div className="mb-3">
                <img data-testid="feedback-shot" src={shot} alt={t("feedback.screenshot")} className="max-h-48 rounded border border-line" />
                <button type="button" className="mt-1 text-xs underline" onClick={() => void attachScreenshot(false)}>
                  {t("feedback.removeScreenshot")}
                </button>
              </div>
            )}
            <button type="button" data-testid="feedback-preview" className="mb-2 text-xs underline" onClick={() => void showPreview()}>
              {t("feedback.preview")}
            </button>
            {preview && (
              <pre data-testid="feedback-preview-json" dir="ltr" className="mb-3 max-h-60 overflow-auto rounded bg-surface-2 p-2 font-mono text-2xs">
                {preview}
              </pre>
            )}
            {error && (
              <p data-testid="feedback-error" role="alert" className="mb-2 text-sm text-danger">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {t("feedback.cancel")}
              </Button>
              <Button
                data-testid="feedback-send"
                busy={busy}
                disabled={!available || !message.trim()}
                onClick={() => void send()}
                variant="primary"
              >
                {t("feedback.send")}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
