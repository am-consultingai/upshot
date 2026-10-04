import { describe, expect, it } from "vitest";
import { mixLevel } from "../src/components/RecordingWaveform";
import { hitSpeaker, isMicSlot, nameFor, speakers } from "../src/lib/speakers";
import { en } from "../src/locales/en";

const words = { you: "You", them: "Them", speaker: "Speaker {n}", mic: "Mic {n}" };
const t = (key: keyof typeof en) => en[key];

describe("speaker slots from both tracks (D85)", () => {
  it("knows the microphone's slots", () => {
    expect(isMicSlot("ME")).toBe(true);
    expect(isMicSlot("ME_2")).toBe(true);
    expect(isMicSlot("THEM")).toBe(false);
    expect(isMicSlot("THEM_1")).toBe(false);
    expect(isMicSlot("MEGAN")).toBe(false);
  });

  it("one voice on the microphone is you; several are numbered", () => {
    expect(nameFor("ME", {}, [], words).name).toBe("You");
    expect(nameFor("ME_1", {}, [], words).name).toBe("Mic 1");
    expect(nameFor("ME_2", {}, [], words).name).toBe("Mic 2");
    expect(nameFor("THEM_3", {}, [], words).name).toBe("Speaker 3");
  });

  it("every slot can be named, the microphone's included", () => {
    expect(nameFor("ME", { ME: "Dana" }, [], words).name).toBe("Dana");
    expect(nameFor("ME_2", { ME_2: "Avi" }, [], words).name).toBe("Avi");
  });

  it("lists the microphone's voices first, then by talk time", () => {
    const people = speakers(
      [
        { speaker: "THEM_1", start: 0, end: 100, text: "a" },
        { speaker: "ME_2", start: 100, end: 110, text: "b" },
        { speaker: "ME_1", start: 110, end: 130, text: "c" },
      ],
      {},
      [],
      words,
    );
    expect(people.map((person) => person.slot)).toEqual(["ME_1", "ME_2", "THEM_1"]);
    expect(people.map((person) => person.mine)).toEqual([true, true, false]);
    expect(new Set(people.map((person) => person.colour)).size).toBe(3);
  });

  it("a search hit says who spoke", () => {
    expect(hitSpeaker({ speaker: "ME" }, t)).toBe("You");
    expect(hitSpeaker({ speaker: "ME_2" }, t)).toBe("Mic 2");
    expect(hitSpeaker({ speaker: "ME_2", speaker_name: "Avi" }, t)).toBe("Avi");
    expect(hitSpeaker({ speaker: "THEM", speaker_name: null }, t)).toBe("Them");
  });
});

describe("the live waveform mixes both tracks", () => {
  it("adds the two in power", () => {
    expect(mixLevel(0, 0)).toBe(0);
    expect(mixLevel(0.3, 0)).toBeCloseTo(0.3);
    expect(mixLevel(0, 0.4)).toBeCloseTo(0.4);
    expect(mixLevel(0.3, 0.4)).toBeCloseTo(0.5);
  });
});
