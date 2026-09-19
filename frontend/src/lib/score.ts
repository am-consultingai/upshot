/**
 * How well a candidate matches what has been typed.
 *
 * A port of the shape cmdk uses (itself from Command-T): a subsequence match,
 * scored by *how* each character was reached. The weights are the whole point —
 * continuing a run is worth far more than jumping, and a jump to the start of a
 * word is worth far more than a jump into the middle of one.
 *
 * That ratio is what separates a palette that feels clairvoyant from one that
 * feels broken. With flat weights, "sum" matches "S-ettings, s-ummary…" by
 * scavenging letters from anywhere, and the arbitrary match outranks the obvious
 * one. Here an arbitrary jump costs an order of magnitude more than a word jump,
 * so the obvious answer wins.
 *
 * Returns 0 for no match; anything above 0 is a match, and the caller decides
 * what to do with the ordering.
 */

const CONTINUE = 1;
const WORD_JUMP = 0.9;
const JUMP = 0.17;
const CASE_MISMATCH = 0.9999;
/** Matching late in the string is slightly worse than matching at the front. */
const LATE_START = 0.9;

function isBoundary(text: string, index: number): boolean {
  if (index === 0) return true;
  const previous = text[index - 1];
  return previous === " " || previous === "-" || previous === "_" || previous === "/";
}

export function score(candidate: string, query: string): number {
  const needle = query.trim();
  if (needle === "") return 1;
  if (needle.length > candidate.length) return 0;

  const haystack = candidate.toLowerCase();
  const lowered = needle.toLowerCase();

  let total = 0;
  let at = 0;
  let previousIndex = -1;

  for (let index = 0; index < lowered.length; index += 1) {
    const found = haystack.indexOf(lowered[index], at);
    if (found === -1) return 0;

    let step: number;
    if (found === previousIndex + 1) step = CONTINUE;
    else if (isBoundary(haystack, found)) step = WORD_JUMP;
    else step = JUMP;

    if (candidate[found] !== needle[index]) step *= CASE_MISMATCH;
    total += step;
    previousIndex = found;
    at = found + 1;
  }

  // Normalised by query length so a long query cannot outscore a short one purely
  // by having more characters to add up.
  let result = total / lowered.length;
  const firstHit = haystack.indexOf(lowered[0]);
  if (firstHit > 0) result *= LATE_START;
  return result;
}

/**
 * A small, decaying boost for things chosen often and recently.
 *
 * Kept multiplicative and bounded well above zero on purpose: a frecency signal
 * must reorder results, never remove one. An item that scores a match and then
 * gets multiplied to nothing simply vanishes, and the person who typed its exact
 * name is left staring at an empty list.
 */
export function boost(hits: number, lastUsed: number, now: number): number {
  if (hits <= 0) return 1;
  const days = Math.max(0, (now - lastUsed) / 86_400_000);
  const decay = Math.exp(-days / 14);
  return 1 + Math.min(0.6, 0.15 * Math.log2(1 + hits) * decay);
}
