import { useQuery } from "@tanstack/react-query";
import { api, type LlmProvider } from "../api";

/**
 * What still needs setting up, for the "!" on Settings and on its sections.
 *
 * Two things, because they are the two that make the app quietly worse rather than
 * visibly broken: with no calendar, meetings are untitled and nothing is "Up next";
 * with no key or sign-in for the chosen summarizer, every meeting records and
 * transcribes and then fails at the summary.
 *
 * The calendar is only flagged when this build can connect one at all — a build with
 * no Google client has nothing to offer, and a "!" you cannot clear teaches people to
 * ignore it. The summarizer is flagged for the provider actually chosen: a key stored
 * for another one does not make this one work.
 */
export interface SetupWarnings {
  calendar: boolean;
  summaries: boolean;
}

/** A provider that can summarize right now. */
export function providerReady(provider: LlmProvider): boolean {
  if (provider.needs === "cli") return provider.ready && provider.signed_in !== false;
  return provider.ready;
}

export function summarizerMissing(active: string | undefined, providers: LlmProvider[]): boolean {
  if (!active) return false;
  const chosen = providers.find((provider) => provider.id === active);
  // Not listed ("fake", in development): nothing a person could set up.
  if (!chosen) return false;
  return !providerReady(chosen);
}

export function useSetupWarnings(): SetupWarnings {
  const calendar = useQuery({ queryKey: ["calendar"], queryFn: api.calendarStatus });
  // Shared with the Summaries screen, so connecting there clears the mark here.
  const llm = useQuery({ queryKey: ["llm-status"], queryFn: api.llmStatus, staleTime: 5 * 60_000 });
  return {
    calendar: Boolean(calendar.data?.configured) && calendar.data?.state !== "connected",
    summaries: summarizerMissing(llm.data?.active, llm.data?.providers ?? []),
  };
}
