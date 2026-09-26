import { useEffect, useRef } from "react";

/** How long the burst lasts before it clears itself. */
const DURATION_MS = 3800;
const PIECES = 170;

/** The app's own colours, read from the tokens so confetti follows the theme. */
function palette(): string[] {
  const style = getComputedStyle(document.documentElement);
  return ["--color-accent", "--color-success", "--color-warning", "--color-danger", "--color-accent-quiet"]
    .map((name) => style.getPropertyValue(name).trim())
    .filter(Boolean);
}

/**
 * A burst of confetti over the whole window, once: the app's welcome after first-run
 * setup is finished. Drawn on a canvas that takes no clicks and removes itself.
 * Nobody who asked their system for less motion gets it.
 */
export default function Confetti({ onDone }: { onDone: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const node = canvas.current;
    const context = node?.getContext("2d");
    if (!node || !context || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      onDone();
      return;
    }
    const ratio = window.devicePixelRatio || 1;
    const width = window.innerWidth;
    const height = window.innerHeight;
    node.width = width * ratio;
    node.height = height * ratio;
    context.scale(ratio, ratio);

    const colours = palette();
    const pieces = Array.from({ length: PIECES }, (_, i) => ({
      // Two cannons, one from each lower corner, aimed up and inwards.
      x: i % 2 ? width * 0.1 : width * 0.9,
      y: height * 0.95,
      vx: (i % 2 ? 1 : -1) * (3 + Math.random() * 7),
      vy: -(11 + Math.random() * 9),
      size: 5 + Math.random() * 6,
      spin: Math.random() * Math.PI,
      spinSpeed: (Math.random() - 0.5) * 0.3,
      colour: colours[i % colours.length] || "gold",
    }));

    const started = performance.now();
    let frame = 0;
    const draw = (now: number) => {
      const elapsed = now - started;
      context.clearRect(0, 0, width, height);
      const fade = Math.max(0, 1 - Math.max(0, elapsed - DURATION_MS * 0.6) / (DURATION_MS * 0.4));
      context.globalAlpha = fade;
      for (const piece of pieces) {
        piece.vy += 0.28; // gravity
        piece.vx *= 0.99; // air
        piece.x += piece.vx;
        piece.y += piece.vy;
        piece.spin += piece.spinSpeed;
        context.save();
        context.translate(piece.x, piece.y);
        context.rotate(piece.spin);
        context.fillStyle = piece.colour;
        context.fillRect(-piece.size / 2, -piece.size / 4, piece.size, piece.size / 2);
        context.restore();
      }
      if (elapsed < DURATION_MS) frame = requestAnimationFrame(draw);
      else onDone();
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [onDone]);

  return (
    <canvas
      ref={canvas}
      data-testid="confetti"
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 z-50 h-screen w-screen"
    />
  );
}
