import { describe, expect, it } from "vitest";
import {
  SPARE_BYTES,
  downloadFraction,
  roomForModel,
  setupPending,
} from "../src/lib/speech";

describe("setupPending", () => {
  it("is only an explicit false", () => {
    expect(setupPending({ setup: { done: false } })).toBe(true);
    expect(setupPending({ setup: { done: true } })).toBe(false);
    // A backend from before the setup screen sends no key: never trap anyone on it.
    expect(setupPending({})).toBe(false);
    expect(setupPending(undefined)).toBe(false);
  });
});

describe("the download", () => {
  it("measures progress against the total once there is one", () => {
    expect(downloadFraction({ done_bytes: 0, total_bytes: 0 })).toBeNull();
    expect(downloadFraction({ done_bytes: 50, total_bytes: 200 })).toBe(0.25);
    expect(downloadFraction({ done_bytes: 300, total_bytes: 200 })).toBe(1);
  });

  it("asks for the model plus a gigabyte to spare, less what is already here", () => {
    const model = 1_620_000_000;
    expect(roomForModel({ expected_bytes: model, free_bytes: model + SPARE_BYTES, done_bytes: 0 })).toBe(true);
    expect(roomForModel({ expected_bytes: model, free_bytes: model, done_bytes: 0 })).toBe(false);
    // A resumed download needs room only for the rest.
    expect(roomForModel({ expected_bytes: model, free_bytes: SPARE_BYTES + 10, done_bytes: model - 10 })).toBe(true);
    // Unknown figures are the backend's to judge.
    expect(roomForModel({ expected_bytes: 0, free_bytes: 0, done_bytes: 0 })).toBe(true);
  });
});
