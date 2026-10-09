import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api";
import TranscriptionsPage from "../src/routes/Transcriptions";
import { LIST_KEY } from "../src/lib/transcriptions";
import { scrollKeyTakesOver } from "../src/components/Transcript";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } })),
  );
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

async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <TranscriptionsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
}

const byTestId = (id: string) => container.querySelector(`[data-testid="${id}"]`);

describe("a failed refetch over data already on screen", () => {
  it("keeps the data and does not swap it for the error panel", async () => {
    const list = vi.spyOn(api, "transcriptions").mockResolvedValueOnce({ transcriptions: [] });
    await render();
    expect(byTestId("transcriptions-empty")).not.toBeNull();

    list.mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      await client.refetchQueries({ queryKey: LIST_KEY }).catch(() => undefined);
    });
    await settle();
    expect(client.getQueryState(LIST_KEY)?.status).toBe("error");
    expect(byTestId("transcriptions-error")).toBeNull();
    expect(byTestId("transcriptions-empty")).not.toBeNull();
  });

  it("still shows the error panel when nothing ever loaded", async () => {
    vi.spyOn(api, "transcriptions").mockRejectedValue(new Error("offline"));
    await render();
    expect(byTestId("transcriptions-error")).not.toBeNull();
  });
});

describe("which keys stop the transcript following playback", () => {
  it("counts scrolling keys in the transcript's box or with nothing focused", () => {
    const box = document.createElement("div");
    const line = document.createElement("p");
    box.append(line);
    document.body.append(box);
    expect(scrollKeyTakesOver("ArrowDown", line, box)).toBe(true);
    expect(scrollKeyTakesOver("PageUp", box, box)).toBe(true);
    expect(scrollKeyTakesOver("End", document.body, box)).toBe(true);
    expect(scrollKeyTakesOver("j", line, box)).toBe(false);
    box.remove();
  });

  it("ignores arrows in the sidebar or a field elsewhere", () => {
    const box = document.createElement("div");
    const sidebar = document.createElement("div");
    const input = document.createElement("input");
    box.append(input);
    document.body.append(box, sidebar);
    expect(scrollKeyTakesOver("ArrowDown", sidebar, box)).toBe(false);
    expect(scrollKeyTakesOver("ArrowUp", input, box)).toBe(false);
    box.remove();
    sidebar.remove();
  });
});
