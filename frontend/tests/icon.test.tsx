import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ArrowRight, Search } from "lucide-react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ICON_STROKE, Icon } from "../src/components/Icon";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function svg(node: React.ReactNode): SVGSVGElement {
  act(() => root.render(node));
  const found = host.querySelector("svg");
  if (!found) throw new Error("no svg");
  return found;
}

describe("Icon", () => {
  it("is decorative by default, at the app's size and stroke", () => {
    const el = svg(<Icon icon={Search} />);
    expect(el.getAttribute("aria-hidden")).toBe("true");
    expect(el.classList.contains("ma-icon")).toBe(true);
    expect(el.getAttribute("stroke-width")).toBe(String(ICON_STROKE));
    expect(el.querySelector("[vector-effect=non-scaling-stroke]")).not.toBeNull();
  });

  it("is not hidden once it has a name of its own", () => {
    const el = svg(<Icon icon={Search} aria-label="Search" />);
    expect(el.hasAttribute("aria-hidden")).toBe(false);
    expect(el.getAttribute("aria-label")).toBe("Search");
  });

  it("turns round in a right-to-left direction only when asked", () => {
    expect(svg(<Icon icon={ArrowRight} mirror />).getAttribute("class")).toContain("rtl:-scale-x-100");
    expect(svg(<Icon icon={Search} />).getAttribute("class")).not.toContain("rtl:-scale-x-100");
  });

  it("passes test ids and the call site's classes through", () => {
    const el = svg(<Icon icon={Search} data-testid="glyph" className="size-3 text-tertiary" />);
    expect(el.getAttribute("data-testid")).toBe("glyph");
    expect(el.getAttribute("class")).toContain("size-3 text-tertiary");
  });
});
