import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type LlmProvider } from "../api";
import { useI18n } from "../i18n";
import Button from "./Button";

/** The program a CLI provider runs, as the toast and the card name it. */
const PRODUCT: Record<string, string> = {
  "claude-subscription": "Claude Code",
  "codex-subscription": "Codex",
  "antigravity-subscription": "Antigravity CLI",
};

/** A provider that could write this summary now: signed in, or a key that is set. */
function canRunNow(provider: LlmProvider): boolean {
  if (provider.needs === "cli") return provider.ready && provider.signed_in === true;
  // The local model is always "ready" by this measure, and is not an alternative a
  // signed-out subscription is waiting for.
  return provider.ready && provider.id !== "ollama";
}

/**
 * The summary is held because the provider in Settings is signed out. Rather than a
 * failure and a stack trace: sign in, or let another service that is ready write this
 * one meeting's summary. `null` when the provider is signed in, or nothing is known yet.
 */
export default function SignedOutCard({
  meetingId,
  sensitive,
  onPicked,
}: {
  meetingId: string;
  sensitive: boolean;
  onPicked: () => void;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // The server's held answer, not a fresh one: the pipeline drops it the moment a CLI
  // turns out signed out, so it is already current where it matters.
  const status = useQuery({
    queryKey: ["llm-status", "held"],
    queryFn: () => api.llmStatus(),
    refetchInterval: 15000,
  });
  const pick = useMutation({
    mutationFn: (provider: string) => api.retry(meetingId, "summarize", true, undefined, provider),
    onSuccess: () => {
      onPicked();
      queryClient.invalidateQueries();
    },
  });

  const active = status.data?.providers.find((provider) => provider.id === status.data?.active);
  if (!active || active.needs !== "cli" || active.signed_in !== false) return null;
  const product = PRODUCT[active.id] ?? active.label;
  // A sensitive meeting is summarized on this computer only: nothing else is offered.
  const others = sensitive
    ? []
    : (status.data?.providers ?? []).filter((p) => p.id !== active.id && canRunNow(p));

  return (
    <div data-testid="signed-out" className="mb-4 rounded-lg bg-surface-2 p-3 text-sm">
      <p className="font-medium">{t("meeting.signedOut").replace("{product}", product)}</p>
      <div className="mt-2.5 flex flex-wrap gap-2">
        <Button data-testid="signed-out-sign-in" variant="primary" onClick={() => navigate("/settings#summaries")}>
          {t("meeting.signedOutSignIn")}
        </Button>
        {others.map((provider) => (
          <Button
            key={provider.id}
            data-testid="signed-out-use"
            data-provider={provider.id}
            variant="secondary"
            busy={pick.isPending && pick.variables === provider.id}
            disabled={pick.isPending}
            onClick={() => pick.mutate(provider.id)}
          >
            {t("meeting.signedOutUse").replace("{product}", PRODUCT[provider.id] ?? provider.label)}
          </Button>
        ))}
      </div>
    </div>
  );
}
