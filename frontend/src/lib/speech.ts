import type { ModelStatus } from "../api";

/**
 * Whether first-run setup still has to be shown.
 *
 * Only an explicit `false` counts. A server that does not send the key at all is one
 * from before the setup screen existed, and trapping someone on a screen their backend
 * cannot finish would be worse than not showing it.
 */
export function setupPending(config: Record<string, unknown> | undefined): boolean {
  const setup = config?.setup as { done?: unknown } | undefined;
  return setup?.done === false;
}

/** 0..1 through the download, or null when there is nothing to measure against yet. */
export function downloadFraction(status: Pick<ModelStatus, "done_bytes" | "total_bytes">): number | null {
  if (!status.total_bytes) return null;
  return Math.min(1, Math.max(0, status.done_bytes / status.total_bytes));
}

/** Whether the drive can take the model, with the backend's one gigabyte to spare. */
export const SPARE_BYTES = 1024 ** 3;

export function roomForModel(status: Pick<ModelStatus, "expected_bytes" | "free_bytes" | "done_bytes">): boolean {
  if (!status.expected_bytes || !status.free_bytes) return true; // unknown: let the backend judge
  return status.free_bytes >= Math.max(0, status.expected_bytes - status.done_bytes) + SPARE_BYTES;
}
