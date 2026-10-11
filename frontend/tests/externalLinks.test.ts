import { afterEach, describe, expect, it, vi } from "vitest";

const openLink = vi.fn(async (_url: string) => ({ opened: true }));
vi.mock("../src/api", () => ({ api: { openLink: (url: string) => openLink(url) } }));

import { interceptExternalLinks } from "../src/lib/external";

/*
 * Links leave Upshot through the server, which opens them in the user's own browser
 * profile. One inside a panel that stops click propagation (EventDetails' "Meeting
 * link") never reached the listener, and the app window followed it itself, in its own
 * profile signed in to nothing (z8tj1hfdww).
 */
describe("interceptExternalLinks", () => {
  let stop: (() => void) | undefined;
  afterEach(() => {
    stop?.();
    document.body.innerHTML = "";
    openLink.mockClear();
  });

  function link(inside: HTMLElement): HTMLAnchorElement {
    const anchor = document.createElement("a");
    anchor.href = "https://meet.google.com/abc-defg-hij";
    anchor.target = "_blank";
    inside.appendChild(anchor);
    return anchor;
  }

  it("opens a plain external link through the server", () => {
    stop = interceptExternalLinks();
    const anchor = link(document.body);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    anchor.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(openLink).toHaveBeenCalledWith("https://meet.google.com/abc-defg-hij");
  });

  it("opens a link inside a panel that stops propagation through the server too", () => {
    stop = interceptExternalLinks();
    const panel = document.createElement("div");
    panel.addEventListener("click", (event) => event.stopPropagation());
    document.body.appendChild(panel);
    const anchor = link(panel);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    anchor.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    expect(openLink).toHaveBeenCalledWith("https://meet.google.com/abc-defg-hij");
  });

  it("leaves links to the app itself alone", () => {
    stop = interceptExternalLinks();
    const anchor = document.createElement("a");
    anchor.href = "/m/abc";
    document.body.appendChild(anchor);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 });
    anchor.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
    expect(openLink).not.toHaveBeenCalled();
  });
});
