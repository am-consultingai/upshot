import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, type ActiveJob, type Meeting } from "../src/api";
import { en } from "../src/locales/en";
import { de } from "../src/locales/de";
import { es } from "../src/locales/es";
import { fr } from "../src/locales/fr";
import { he } from "../src/locales/he";
import ActiveJobs from "../src/components/ActiveJobs";
import MeetingCard from "../src/components/MeetingCard";
import {
  ACTIVE_KEY,
  applyFileEvent,
  applyJobProgress,
  formatPercent,
  jobStatus,
  timeLeft,
} from "../src/lib/activeJobs";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const t = (key: keyof typeof en) => en[key];

function activeJob(overrides: Partial<ActiveJob> = {}): ActiveJob {
  return {
    kind: "meeting",
    id: "m1",
    title: "Weekly sync",
    client: "meeting",
    stage: "transcribe",
    state: "running",
    position: null,
    waiting_reason: null,
    phase: "transcribe",
    progress: 0.1,
    eta_s: null,
    queued_at: "2026-10-08T09:00:00.000+03:00",
    started_at: "2026-10-08T09:00:01.000+03:00",
    cancellable: false,
    ...overrides,
  };
}

function meeting(overrides: Partial<Meeting> = {}): Meeting {
  return {
    id: "m1",
    title: "Weekly sync",
    state: "TRANSCRIBING",
    started_at: "2026-10-08T08:00:00.000+03:00",
    ended_at: "2026-10-08T08:40:00.000+03:00",
    duration_s: 2400,
    language: null,
    summary_language: null,
    source: "manual",
    sensitive: 0,
    folder: "/x",
    error: null,
    ...overrides,
  } as Meeting;
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let served: ActiveJob[];

beforeEach(() => {
  served = [];
  // Anything else a component asks for (calendar accounts) answers empty.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } })),
  );
  vi.spyOn(api, "activeJobs").mockImplementation(async () => ({ jobs: served }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

const byTestId = (id: string) => container.querySelector(`[data-testid="${id}"]`);

describe("the meeting card", () => {
  it("shows the percent a progress event carries, and the time left once measured", async () => {
    served = [activeJob({ progress: 0.1 })];
    await render(<MeetingCard meeting={meeting()} />);
    expect(byTestId("meeting-progress")?.textContent).toBe("10%");
    expect(byTestId("eta")).toBeNull();

    await act(async () => {
      applyJobProgress(client, {
        action: "progress",
        meeting_id: "m1",
        stage: "transcribe",
        phase: "diarize",
        progress: 0.42,
        eta_s: 360,
      });
    });
    await settle();
    expect(byTestId("meeting-progress")?.textContent).toBe("42%");
    expect(byTestId("eta")?.textContent).toBe("about 6 min left");
  });

  it("invents no time for a stage that cannot say: the fake constant is gone", async () => {
    served = [activeJob({ stage: "summarize", phase: null, progress: null })];
    await render(<MeetingCard meeting={meeting({ state: "SUMMARIZING" })} />);
    expect(byTestId("meeting-state")?.textContent).toBe("summarizing");
    expect(byTestId("meeting-progress")).toBeNull();
    expect(byTestId("eta")).toBeNull();
  });

  it("says nothing of progress for a meeting with nothing in the queue", async () => {
    await render(<MeetingCard meeting={meeting({ state: "TRANSCRIBING" })} />);
    expect(byTestId("meeting-progress")).toBeNull();
    expect(byTestId("eta")).toBeNull();
  });
});

describe("Settings: running and waiting", () => {
  it("lists meetings and files in order, Claude's among them, with percent and cancel", async () => {
    served = [
      activeJob({ progress: 0.5, phase: "transcribe", eta_s: 90 }),
      activeJob({
        kind: "file",
        id: "tr_claude",
        title: "interview.mp4",
        client: "mcp",
        stage: null,
        state: "pending",
        position: 2,
        waiting_reason: "queue",
        phase: null,
        progress: null,
        started_at: null,
        cancellable: true,
      }),
    ];
    await render(<ActiveJobs />);
    const rows = [...container.querySelectorAll('[data-testid="active-job"]')];
    expect(rows.map((row) => row.getAttribute("data-id"))).toEqual(["m1", "tr_claude"]);

    const [first, second] = rows;
    expect(first.querySelector('[data-testid="active-job-client"]')?.textContent).toBe("Meeting");
    expect(first.querySelector('[data-testid="active-job-status"]')?.textContent).toBe(
      "Transcribing · transcribing · 50%",
    );
    expect(first.querySelector('[data-testid="active-job-eta"]')?.textContent).toBe(
      "about 2 min left",
    );
    expect(first.querySelector('[data-testid="active-job-link"]')?.getAttribute("href")).toBe("/m/m1");
    expect(first.querySelector('[data-testid="active-job-cancel"]')).toBeNull();

    expect(second.querySelector('[data-testid="active-job-client"]')?.textContent).toBe("Claude");
    expect(second.querySelector('[data-testid="active-job-status"]')?.textContent).toBe(
      "Waiting · position 2",
    );
    expect(second.querySelector('[data-testid="active-job-link"]')?.getAttribute("href")).toBe(
      "/transcriptions",
    );
    expect(second.querySelector('[data-testid="active-job-cancel"]')).not.toBeNull();
  });

  it("drops a job once it finishes, and says when nothing is left", async () => {
    served = [
      activeJob({
        kind: "file",
        id: "tr_a",
        title: "clip.wav",
        client: "api",
        stage: null,
        progress: 0.9,
        phase: "diarize",
        cancellable: true,
      }),
    ];
    await render(<ActiveJobs />);
    expect(container.querySelectorAll('[data-testid="active-job"]')).toHaveLength(1);

    served = [];
    await act(async () => {
      applyFileEvent(client, { id: "tr_a", state: "done", phase: null, progress: 1 });
    });
    await settle();
    expect(container.querySelectorAll('[data-testid="active-job"]')).toHaveLength(0);
    expect(byTestId("active-jobs-empty")?.textContent).toBe("Nothing is being transcribed.");
  });
});

describe("the words", () => {
  it("a file's progress event patches its row without a refetch", () => {
    const local = new QueryClient();
    local.setQueryData(ACTIVE_KEY, { jobs: [activeJob({ kind: "file", id: "tr_a", stage: null })] });
    const refetch = vi.spyOn(local, "invalidateQueries");
    applyFileEvent(local, { id: "tr_a", state: "running", phase: "transcribe", progress: 0.3, eta_s: 40 });
    const [job] = local.getQueryData<{ jobs: ActiveJob[] }>(ACTIVE_KEY)!.jobs;
    expect([job.phase, job.progress, job.eta_s]).toEqual(["transcribe", 0.3, 40]);
    expect(refetch).not.toHaveBeenCalled();
  });

  it("a progress event for a meeting the list does not have refetches the list", () => {
    const local = new QueryClient();
    local.setQueryData(ACTIVE_KEY, { jobs: [] });
    const refetch = vi.spyOn(local, "invalidateQueries");
    applyJobProgress(local, {
      action: "progress",
      meeting_id: "m9",
      stage: "transcribe",
      phase: "language",
      progress: 0.05,
      eta_s: null,
    });
    expect(refetch).toHaveBeenCalled();
  });

  it("percent and time left, in both languages", () => {
    expect(formatPercent(0.424, "en")).toBe("42%");
    expect(formatPercent(0.424, "he")).toContain("42");
    expect(timeLeft(null, t)).toBeNull();
    expect(timeLeft(30, t)).toBe("under a minute left");
    expect(timeLeft(3600 + 600, (key) => he[key])).toContain("עוד כ־");
  });

  it("time left is rounded as the estimate it is", () => {
    expect(timeLeft(undefined, t)).toBeNull();
    expect(timeLeft(0, t)).toBe("under a minute left");
    expect(timeLeft(59, t)).toBe("under a minute left");
    expect(timeLeft(60, t)).toBe("about a minute left");
    expect(timeLeft(89, t)).toBe("about a minute left");
    expect(timeLeft(90, t)).toBe("about 2 min left");
    expect(timeLeft(124, t)).toBe("about 2 min left");
    expect(timeLeft(195, t)).toBe("about 3 min left");
    expect(timeLeft(666, t)).toBe("about 11 min left");
    expect(timeLeft(59 * 60 + 40, t)).toBe("about 1 h left");
    expect(timeLeft(3600 + 22 * 60, t)).toBe("about 1 h 20 min left");
    expect(timeLeft(2 * 3600 + 2 * 60, t)).toBe("about 2 h left");
  });

  it("every language says the short waits in its own words", () => {
    for (const locale of [he, de, es, fr]) {
      const say = (key: keyof typeof en) => locale[key];
      expect(timeLeft(75, say)).toBe(locale["progress.leftAboutMinute"]);
      expect(timeLeft(30, say)).toBe(locale["progress.leftUnderMinute"]);
      expect(timeLeft(600, say)).not.toContain("{time}");
    }
  });

  it("a summary is named, with no percentage", () => {
    const status = jobStatus(activeJob({ stage: "summarize", phase: null, progress: null }), t, "en");
    expect(status).toBe("Summarizing");
  });
});
