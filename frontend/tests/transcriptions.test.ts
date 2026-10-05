import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, type Transcription } from "../src/api";
import { en } from "../src/locales/en";
import {
  DEFAULT_OPTIONS,
  LIST_KEY,
  applyEvent,
  canCancel,
  canRetry,
  loadOptions,
  saveOptions,
  stateLabel,
  upload,
  uploadBody,
} from "../src/lib/transcriptions";

const t = (key: keyof typeof en) => en[key];

function job(overrides: Partial<Transcription> = {}): Transcription {
  return {
    id: "tr_a",
    state: "pending",
    source_name: "clip.mp4",
    source_kind: "upload",
    size_bytes: 10,
    duration_s: 30,
    options: { language: "auto", diarize: true, prompt: "", max_words_per_cue: 7 },
    client: "ui",
    language: null,
    language_conf: null,
    phase: null,
    progress: 0,
    attempts: 0,
    error: null,
    queued_at: "2026-10-05T10:00:00.000+03:00",
    created_at: "2026-10-05T10:00:00.000+03:00",
    started_at: null,
    finished_at: null,
    position: 2,
    waiting_reason: "queue",
    direction: "ltr",
    links: {},
    ...overrides,
  };
}

describe("what a row says", () => {
  it("names the position while waiting in the queue", () => {
    expect(stateLabel(job(), t)).toBe("Waiting · position 2");
  });
  it("names the reason when something else holds it", () => {
    expect(stateLabel(job({ waiting_reason: "recording" }), t)).toBe("Waiting until the recording ends");
    expect(stateLabel(job({ waiting_reason: "policy:scheduled" }), t)).toBe("Waiting for the nightly window");
  });
  it("names the phase and the progress while running", () => {
    expect(stateLabel(job({ state: "running", phase: "transcribe", progress: 0.426 }), t)).toBe(
      "Transcribing · transcribing 43%",
    );
  });
  it("offers cancel while it can still be stopped, and retry once it stopped", () => {
    expect(canCancel(job())).toBe(true);
    expect(canCancel(job({ state: "done" }))).toBe(false);
    expect(canRetry(job({ state: "failed" }))).toBe(true);
    expect(canRetry(job({ state: "done" }))).toBe(false);
  });
});

describe("options remembered per browser", () => {
  it("round-trips, and falls back to the defaults on anything odd", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    saveOptions({ language: "he", diarize: false, prompt: "דיברה" }, storage);
    expect(loadOptions(storage)).toEqual({ language: "he", diarize: false, prompt: "דיברה" });
    store.set("upshot.transcription.options", "{not json");
    expect(loadOptions(storage)).toEqual(DEFAULT_OPTIONS);
    expect(loadOptions(null)).toEqual(DEFAULT_OPTIONS);
  });
});

describe("the upload", () => {
  it("sends the options as fields and the file last", () => {
    const body = uploadBody(new Blob(["x"]), "a.wav", { language: "he", diarize: false, prompt: "  " });
    expect([...body.keys()]).toEqual(["language", "diarize", "file"]);
    expect(body.get("diarize")).toBe("false");
  });

  it("reports progress and resolves with the job", async () => {
    const sent: { headers: Record<string, string>; body?: unknown } = { headers: {} };
    const fake = {
      upload: {} as { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void },
      status: 0,
      responseText: "",
      open: vi.fn(),
      setRequestHeader: (k: string, v: string) => {
        sent.headers[k] = v;
      },
      send(body: unknown) {
        sent.body = body;
        this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
        this.status = 202;
        this.responseText = JSON.stringify(job());
        this.onload?.();
      },
      onload: undefined as undefined | (() => void),
      abort: vi.fn(),
    };
    const seen: number[] = [];
    const result = await upload(
      new File(["x"], "a.wav"),
      DEFAULT_OPTIONS,
      (fraction) => seen.push(fraction),
      () => fake as unknown as XMLHttpRequest,
    ).done;
    expect(result.id).toBe("tr_a");
    expect(seen).toEqual([0.5]);
    expect(sent.headers["X-Upshot-Client"]).toBe("ui");
    expect(sent.body).toBeInstanceOf(FormData);
  });

  it("rejects with the server's own words", async () => {
    const fake = {
      upload: {},
      status: 0,
      responseText: "",
      open: vi.fn(),
      setRequestHeader: vi.fn(),
      send() {
        this.status = 415;
        this.responseText = JSON.stringify({ detail: "clip.mp4 has no audio" });
        this.onload?.();
      },
      onload: undefined as undefined | (() => void),
      abort: vi.fn(),
    };
    await expect(
      upload(new File(["x"], "clip.mp4"), DEFAULT_OPTIONS, () => {}, () => fake as unknown as XMLHttpRequest).done,
    ).rejects.toThrow("clip.mp4 has no audio");
  });
});

describe("request() and FormData", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("leaves a FormData body's own Content-Type alone", async () => {
    const fetched = vi.fn(async () => new Response("{}", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetched);
    await api.cancelTranscription("tr_a");
    const plain = (fetched.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(plain.headers).has("X-CSRF-Token")).toBe(true);
    // JSON bodies still get JSON; a FormData body is never given one.
    await api.putSettings({ a: 1 });
    const json = (fetched.mock.calls[1] as unknown as [string, RequestInit])[1];
    expect(new Headers(json.headers).get("Content-Type")).toBe("application/json");
  });
});

describe("live events touch the list only", () => {
  it("patches progress in place, drops a deleted job, and refetches the list on a new state", () => {
    const client = new QueryClient();
    client.setQueryData(LIST_KEY, { transcriptions: [job({ state: "running", phase: "transcribe", progress: 0.1 })] });
    client.setQueryData(["meetings"], { meetings: [] });
    const invalidate = vi.spyOn(client, "invalidateQueries");

    applyEvent(client, { id: "tr_a", state: "running", phase: "transcribe", progress: 0.5 });
    const list = client.getQueryData<{ transcriptions: Transcription[] }>(LIST_KEY)!;
    expect(list.transcriptions[0].progress).toBe(0.5);
    expect(invalidate).not.toHaveBeenCalled();

    applyEvent(client, { id: "tr_a", state: "done", phase: null, progress: 1 });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: LIST_KEY, exact: true });

    applyEvent(client, { id: "tr_a", state: "deleted", phase: null, progress: 0 });
    expect(client.getQueryData<{ transcriptions: Transcription[] }>(LIST_KEY)!.transcriptions).toEqual([]);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });
});
