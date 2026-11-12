import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/*
 * Chrome and Edge offer to save any password field as a website password, whatever its
 * autocomplete says: setup on machine B offered "Save password?" for a Gemini key
 * (2026-11-12). API keys are masked text fields (`secret-input`) instead.
 */
const SRC = join(__dirname, "..", "src");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("API key fields", () => {
  it("no field is a password field", () => {
    const found = sources(SRC).filter((path) => /type=["{]?["']?password/.test(readFileSync(path, "utf8")));
    expect(found).toEqual([]);
  });

  it("the setup and Settings key fields are masked", () => {
    for (const file of ["setup/KeyStep.tsx", "components/ProviderSettings.tsx"]) {
      expect(readFileSync(join(SRC, file), "utf8")).toContain("secret-input");
    }
    expect(readFileSync(join(SRC, "index.css"), "utf8")).toMatch(/\.secret-input\s*{\s*-webkit-text-security:\s*disc/);
  });
});
