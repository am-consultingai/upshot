import type { MessageKey } from "../locales/en";

/** What the three detection modes mean, said in terms of what happens to you. */
export const DETECTION_MODES = [
  { value: "shadow", label: "settings.detectionWatch" },
  { value: "on", label: "settings.detectionAuto" },
  { value: "off", label: "settings.detectionOff" },
] as const satisfies readonly { value: string; label: MessageKey }[];
