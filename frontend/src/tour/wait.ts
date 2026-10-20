/** Resolves once `find` returns an element, or after `timeout` either way. */
export function waitFor(find: () => Element | null, timeout = 6000): Promise<void> {
  return new Promise((resolve) => {
    // The page's own clock, which a wall-clock change (or a test's fixed time) cannot stop.
    const until = performance.now() + timeout;
    const look = () => {
      if (find() || performance.now() > until) resolve();
      else window.setTimeout(look, 50);
    };
    look();
  });
}
