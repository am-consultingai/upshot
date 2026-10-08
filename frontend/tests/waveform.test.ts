import { describe, expect, it } from "vitest";
import { silentSeconds } from "../src/components/RecordingWaveform";

/** `n` readings at a level, 20 a second as the server sends them. */
function run(n: number, level: number, paused = false) {
  return Array.from({ length: n }, () => ({ me: level, them: level, paused }));
}

const SPEECH = 0.05; // about -26 dBFS
const HISS = 0.001; // -60 dBFS

describe("the waveform's silence indication", () => {
  it("is not raised by the gaps between sentences", () => {
    expect(silentSeconds([...run(100, SPEECH), ...run(40, HISS)])).toBe(0);
  });

  it("says how long, in whole seconds, once it has been three", () => {
    expect(silentSeconds([...run(100, SPEECH), ...run(60, HISS)])).toBe(3);
    expect(silentSeconds([...run(100, SPEECH), ...run(250, HISS)])).toBe(12);
  });

  it("goes the moment anything is heard on either track", () => {
    const readings = [...run(200, HISS), { me: 0, them: SPEECH, paused: false }];
    expect(silentSeconds(readings)).toBe(0);
  });

  it("a pause is not silence", () => {
    expect(silentSeconds(run(200, 0, true))).toBe(0);
  });
});
