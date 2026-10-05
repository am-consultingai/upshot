import { api } from "../api";

/**
 * Errors the page did not expect, handed to the local server (D87, C5). The server
 * decides whether anything goes further: only with the user's yes, and only what its
 * scrubber keeps. The browser itself never talks to Sentry.
 *
 * Each distinct error is posted once per session, and at most `MAX_PER_SESSION` in all:
 * a render loop that throws on every frame is one report, not thousands.
 */
const MAX_PER_SESSION = 10;
const posted = new Set<string>();
let reporting = false;

export function reportClientError(kind: string, error: unknown): void {
  if (reporting || posted.size >= MAX_PER_SESSION) return;
  const { message, stack } = describe(error);
  const key = `${kind}|${message}|${stack.split("\n", 2)[1] ?? ""}`;
  if (posted.has(key)) return;
  posted.add(key);
  reporting = true;
  void api
    .reportClientError({ kind, message, stack })
    .catch(() => undefined)
    .finally(() => {
      reporting = false;
    });
}

/** Window-level errors and promises nobody caught. Called once, at start. */
export function installErrorHandlers(): void {
  window.addEventListener("error", (event) => reportClientError("error", event.error ?? event.message));
  window.addEventListener("unhandledrejection", (event) => reportClientError("rejection", event.reason));
}

function describe(error: unknown): { message: string; stack: string } {
  if (error instanceof Error) {
    return { message: error.message.slice(0, 2000), stack: (error.stack ?? "").slice(0, 20000) };
  }
  return { message: String(error).slice(0, 2000), stack: "" };
}
