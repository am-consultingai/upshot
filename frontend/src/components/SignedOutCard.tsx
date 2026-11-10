import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, reason, type LlmProvider } from "../api";
import { useI18n } from "../i18n";
import Button from "./Button";
import { toast } from "./Toaster";

/**
 * A provider that could write this summary now: a signed-in CLI, or an API provider whose
 * key is set. Not "Transcripts only", and not the local model, whose "ready" says only
 * that it is configured.
 */
export function canRunNow(provider: LlmProvider): boolean {
  // Not a CLI another summary is already waiting on: its status can read signed in over
  // a session it refuses, and the run would only wait again.
  if (provider.needs === "cli") return provider.ready && provider.signed_in === true && !provider.waiting;
  return provider.needs === "key" && provider.ready;
}

/**
 * What the card offers instead: every provider that could run now, except the one waited
 * for, and except the usual one when the job waits on the fallback — the usual provider's
 * allowance is spent then, and picking it would park the summary until its reset. Nothing
 * for a sensitive meeting, which is summarized on this computer only.
 */
export function alternatives(
  providers: LlmProvider[],
  at: { waitingFor: string; active?: string; fallback?: string; sensitive: boolean },
): LlmProvider[] {
  if (at.sensitive) return [];
  const usualIsOut = Boolean(at.fallback) && at.waitingFor === at.fallback;
  return providers.filter(
    (row) => row.id !== at.waitingFor && !(usualIsOut && row.id === at.active) && canRunNow(row),
  );
}

/**
 * The summary waits because `provider` is signed out — as the job itself says
 * (`waiting_for`), whatever the CLI's own status reads. Rather than a failure
 * and a stack trace: sign in, or let another service that is ready write this one
 * meeting's summary.
 */
export default function SignedOutCard({
  meetingId,
  provider,
  sensitive,
  onPicked,
}: {
  meetingId: string;
  provider: string;
  sensitive: boolean;
  onPicked: () => void;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // The server's held answer, not a fresh one, and no polling: asking runs the CLIs, and
  // one of them flashes a console window (see lib/refetchAll.ts). Asked again when the
  // window comes back to the front, as after signing in from Settings or a terminal.
  const status = useQuery({
    queryKey: ["llm-status", "held"],
    queryFn: () => api.llmStatus(),
    refetchOnWindowFocus: true,
  });
  const pick = useMutation({
    mutationFn: (other: string) => api.retry(meetingId, "summarize", true, undefined, other),
    onSuccess: () => {
      onPicked();
      queryClient.invalidateQueries({ queryKey: ["meeting", meetingId] });
      queryClient.invalidateQueries({ queryKey: ["attention"] });
    },
    onError: (error) => toast({ title: reason(error), tone: "danger" }),
  });
  // Waiting on another provider now (the picked one is signed out too): a new choice.
  const { reset } = pick;
  useEffect(() => reset(), [provider, reset]);

  const providers = status.data?.providers ?? [];
  const named = (row: LlmProvider | undefined, id: string) => row?.product ?? row?.label ?? id;
  const product = named(providers.find((row) => row.id === provider), provider);
  const others = alternatives(providers, {
    waitingFor: provider,
    active: status.data?.active,
    fallback: status.data?.fallback,
    sensitive,
  });

  return (
    <div data-testid="signed-out" className="mb-4 rounded-lg bg-surface-2 p-3 text-sm">
      <p className="font-medium">{t("meeting.signedOut").replace("{product}", product)}</p>
      <div className="mt-2.5 flex flex-wrap gap-2">
        <Button data-testid="signed-out-sign-in" variant="primary" onClick={() => navigate("/settings#summaries")}>
          {t("meeting.signedOutSignIn")}
        </Button>
        {others.map((row) => (
          <Button
            key={row.id}
            data-testid="signed-out-use"
            data-provider={row.id}
            variant="secondary"
            busy={pick.isPending && pick.variables === row.id}
            disabled={pick.isPending || pick.isSuccess}
            onClick={() => pick.mutate(row.id)}
          >
            {t("meeting.signedOutUse").replace("{product}", named(row, row.id))}
          </Button>
        ))}
      </div>
    </div>
  );
}
