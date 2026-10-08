/** One line of a diff: kept as it was, added, or removed. */
export interface DiffLine {
  kind: "same" | "add" | "remove";
  text: string;
}

/**
 * A line diff, by longest common subsequence.
 *
 * Enough for showing how an edited prompt differs from the shipped one: a few
 * hundred lines at most, so the quadratic table is a few hundred thousand cells and
 * no dependency is worth carrying for it. Removals come before additions where a
 * line was replaced, which is how every diff reader expects to see a change.
 */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  // lcs[i][j]: the longest common run of a[i..] and b[j..].
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: "remove", text: a[i] });
      i += 1;
    } else {
      out.push({ kind: "add", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) out.push({ kind: "remove", text: a[i++] });
  while (j < b.length) out.push({ kind: "add", text: b[j++] });
  return out;
}

/**
 * The changed lines with a little of what surrounds them, the rest folded away.
 *
 * `null` stands for a run of unchanged lines left out, so the reader sees that
 * something is skipped rather than two distant changes appearing to be neighbours.
 */
export function hunks(lines: DiffLine[], context = 2): (DiffLine | null)[] {
  const keep = lines.map(() => false);
  lines.forEach((line, index) => {
    if (line.kind === "same") return;
    for (let at = Math.max(0, index - context); at <= Math.min(lines.length - 1, index + context); at += 1) {
      keep[at] = true;
    }
  });
  const out: (DiffLine | null)[] = [];
  lines.forEach((line, index) => {
    if (keep[index]) out.push(line);
    else if (out.length === 0 || out[out.length - 1] !== null) out.push(null);
  });
  return out;
}
