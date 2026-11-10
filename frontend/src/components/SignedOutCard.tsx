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
function canRunNow(provider: LlmProvider): boolean {
  if (provider.needs === "cli") return provider.ready && provider.signed_in === true;
  return provider.needs === "key" && provider.ready;
}

/**
 * The summary waits because `provider` is signed out — as the job itself says, not the
 * CLI's status, which can read "signed in" over an expired session. Rather than a failure
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
  // The server's held answer, not a fresh one: the pipeline drops it the moment a CLI
  // turns out signed out, so asking every CLI again here would only cost time.
  const status = useQuery({
    queryKey: ["llm-status", "held"],
    queryFn: () => api.llmStatus(),
    refetchInterval: 15000,
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

  const providers = status.data?.providers ?? [];
  const named = (row: LlmProvider | undefined, id: string) => row?.product ?? row?.label ?? id;
  const product = named(providers.find((row) => row.id === provider), provider);
  // A sensitive meeting is summarized on this computer only: nothing else is offered.
  const others = sensitive ? [] : providers.filter((row) => row.id !== provider && canRunNow(row));

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
