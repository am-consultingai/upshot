export interface TimelineItem {
  id: string;
  started_at: string;
  duration_s: number | null;
}

export interface PlacedItem extends TimelineItem {
  column: number;
  columns: number;
}

const DEFAULT_DURATION_S = 30 * 60;

function span(item: TimelineItem): [number, number] {
  const start = new Date(item.started_at).getTime();
  return [start, start + (item.duration_s ?? DEFAULT_DURATION_S) * 1000];
}

/**
 * Overlapping meetings on the same day get non-overlapping grid columns; a meeting that
 * overlaps nothing keeps the full width.
 */
export function timelineLayout(items: TimelineItem[]): PlacedItem[] {
  const ordered = [...items].sort(
    (a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime(),
  );
  const placed: PlacedItem[] = [];
  let cluster: PlacedItem[] = [];
  let clusterEnd = -Infinity;

  const flush = () => {
    const columns = cluster.reduce((max, item) => Math.max(max, item.column + 1), 0);
    for (const item of cluster) item.columns = columns;
    cluster = [];
  };

  for (const item of ordered) {
    const [start, end] = span(item);
    if (start >= clusterEnd) {
      flush();
      clusterEnd = -Infinity;
    }
    const taken = new Set(
      cluster.filter((other) => span(other)[1] > start).map((other) => other.column),
    );
    let column = 0;
    while (taken.has(column)) column += 1;
    const entry: PlacedItem = { ...item, column, columns: 1 };
    cluster.push(entry);
    placed.push(entry);
    clusterEnd = Math.max(clusterEnd, end);
  }
  flush();
  return placed;
}

export function groupByDay<T extends { started_at: string }>(items: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = item.started_at.slice(0, 10);
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
}
