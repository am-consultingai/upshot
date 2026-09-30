import { api } from "../api";

/**
 * Links that leave Upshot open in the user's own browser, in the profile they were last
 * in (ClickUp z8tj1hca86). Upshot's window runs in a browser profile of its own, so a link
 * it opened itself would land there, signed in to nothing: the server opens it instead.
 * Where the server cannot (not Windows), the page opens it as it always did.
 */
export async function openExternal(url: string): Promise<void> {
  try {
    const { opened } = await api.openLink(url);
    if (opened) return;
  } catch {
    // Fall through: better a tab in the wrong profile than no page at all.
  }
  window.open(url, "_blank", "noreferrer");
}

/** http(s) and not this app's own origin. */
export function isExternal(href: string, origin = window.location.origin): boolean {
  try {
    const url = new URL(href, origin);
    return (url.protocol === "http:" || url.protocol === "https:") && url.origin !== origin;
  } catch {
    return false;
  }
}

/** Route every click on an external link through `openExternal`. Installed once. */
export function interceptExternalLinks(root: Document = document): () => void {
  const onClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0) return;
    const anchor = (event.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!anchor || !isExternal(anchor.href)) return;
    event.preventDefault();
    void openExternal(anchor.href);
  };
  root.addEventListener("click", onClick);
  return () => root.removeEventListener("click", onClick);
}
