/** Resolves once `find` returns an element, or after `timeout` either way. */
export function waitFor(find: () => Element | null, timeout = 6000): Promise<void> {
  return new Promise((resolve) => {
    const until = Date.now() + timeout;
    const look = () => {
      if (find() || Date.now() > until) resolve();
      else window.setTimeout(look, 50);
    };
    look();
  });
}
