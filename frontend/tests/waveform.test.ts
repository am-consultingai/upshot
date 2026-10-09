import { describe, expect, it } from "vitest";
import { silentRun, silentSeconds, type Reading } from "../src/components/RecordingWaveform";

/** `n` readings at a level, 20 a second as the server sends them. */
function run(n: number, level: number, paused = false): Reading[] {
  return Array.from({ length: n }, () => ({ me: level, them: level, paused }));
}

/** The seconds of silence the bar shows after these readings, fed one at a time. */
function shown(readings: readonly Reading[]): number {
  return silentSeconds(readings.reduce(silentRun, 0));
}

const SPEECH = 0.05; // about -26 dBFS
const HISS = 0.001; // -60 dBFS

describe("the waveform's silence indication", () => {
  it("is not raised by the gaps between sentences", () => {
    expect(shown([...run(100, SPEECH), ...run(40, HISS)])).toBe(0);
  });

  it("says how long, in whole seconds, once it has been three", () => {
    expect(shown([...run(100, SPEECH), ...run(60, HISS)])).toBe(3);
    expect(shown([...run(100, SPEECH), ...run(250, HISS)])).toBe(12);
  });

  it("goes the moment anything is heard on either track", () => {
    expect(shown([...run(200, HISS), { me: 0, them: SPEECH, paused: false }])).toBe(0);
  });

  it("a pause is not silence", () => {
    expect(shown(run(200, 0, true))).toBe(0);
  });

  it("keeps counting past the drawing history's 1500 readings (75 s)", () => {
    // Two minutes of nothing: the count is kept beside the capped history, not read from it.
    expect(shown([...run(20, SPEECH), ...run(2400, HISS)])).toBe(120);
  });
});
