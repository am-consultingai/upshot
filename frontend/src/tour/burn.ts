/**
 * The fire at the end of the tour: the sample pieces on screen burn from the bottom up.
 *
 * Only what the caller hands it, and the caller hands it only sample pieces
 * (`[data-tour-sample]`, and the calendar's blocks whose ids start with the sample prefix).
 * One canvas over the window draws the flames; each piece is masked away below a rising,
 * ragged edge. Reduced motion gets a short fade instead.
 */

const MAX_PARTICLES = 900;

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  max: number;
  size: number;
  kind: "flame" | "ember" | "smoke";
}

/** The outermost of the given pieces that are on screen now. */
export function onScreen(pieces: Element[]): HTMLElement[] {
  const inside = pieces.filter((piece): piece is HTMLElement => piece instanceof HTMLElement);
  return inside.filter((piece) => {
    if (inside.some((other) => other !== piece && other.contains(piece))) return false;
    const box = piece.getBoundingClientRect();
    return (
      box.width > 0 &&
      box.height > 0 &&
      box.right > 0 &&
      box.left < window.innerWidth &&
      box.bottom > 0 &&
      box.top < window.innerHeight
    );
  });
}

function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export function burn(pieces: HTMLElement[]): Promise<void> {
  return new Promise((resolve) => {
    if (pieces.length === 0) {
      resolve();
      return;
    }
    if (reducedMotion()) {
      for (const piece of pieces) {
        piece.style.transition = "opacity 300ms";
        piece.style.opacity = "0";
      }
      window.setTimeout(resolve, 320);
      return;
    }
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) {
      resolve();
      return;
    }
    const ratio = window.devicePixelRatio || 1;
    const width = window.innerWidth;
    const height = window.innerHeight;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.dataset.testid = "tour-fire";
    canvas.setAttribute("aria-hidden", "true");
    Object.assign(canvas.style, {
      position: "fixed",
      inset: "0",
      width: `${width}px`,
      height: `${height}px`,
      zIndex: "86",
      pointerEvents: "none",
    });
    document.body.append(canvas);
    context.scale(ratio, ratio);

    const items = pieces.map((piece, index) => {
      const box = piece.getBoundingClientRect();
      const low = Math.min(box.bottom, height);
      const high = Math.max(box.top, 0);
      return { piece, box, low, high, delay: index * 120, length: 700 + Math.min(900, (low - high) * 2.2) };
    });
    const particles: Particle[] = [];
    const began = performance.now();
    const noise = (x: number, t: number) => Math.sin(x * 0.09 + t * 0.004) * 0.6 + Math.sin(x * 0.23 - t * 0.007) * 0.4;

    const frame = (now: number) => {
      context.clearRect(0, 0, width, height);
      let burning = false;
      for (const item of items) {
        const progress = Math.min(Math.max((now - began - item.delay) / item.length, 0), 1);
        if (progress < 1) burning = true;
        const { box, low, high, piece } = item;
        // The edge rises from the piece's visible bottom to a little past its top.
        const edge = low - progress * (low - high + 18);
        const gone = ((box.bottom - edge) / box.height) * 100;
        const mask = `linear-gradient(to top, transparent ${gone - 1}%, #000 ${gone + 7}%)`;
        piece.style.maskImage = mask;
        piece.style.setProperty("-webkit-mask-image", mask);
        if (progress >= 1) {
          piece.style.visibility = "hidden";
          continue;
        }
        if (progress <= 0) continue;

        const points: [number, number][] = [];
        for (let x = box.left; x <= box.right + 6; x += 6) points.push([x, edge + noise(x, now) * 5]);
        const trace = () => {
          context.beginPath();
          points.forEach(([x, y], index) => (index === 0 ? context.moveTo(x, y) : context.lineTo(x, y)));
        };
        // Char above the edge, then the glowing edge itself.
        const char = context.createLinearGradient(0, edge - 16, 0, edge + 4);
        char.addColorStop(0, "rgba(40,18,6,0)");
        char.addColorStop(0.7, "rgba(40,18,6,.75)");
        char.addColorStop(1, "rgba(20,8,2,.9)");
        context.fillStyle = char;
        trace();
        context.lineTo(box.right + 6, edge - 16);
        context.lineTo(box.left, edge - 16);
        context.closePath();
        context.fill();
        context.save();
        context.strokeStyle = "rgba(255,150,40,.95)";
        context.lineWidth = 2;
        context.shadowColor = "rgba(255,110,20,1)";
        context.shadowBlur = 12;
        trace();
        context.stroke();
        context.restore();

        const spawn = Math.ceil(box.width / 10);
        for (let k = 0; k < spawn && particles.length < MAX_PARTICLES; k++) {
          const x = box.left + Math.random() * box.width;
          const y = edge + noise(x, now) * 5;
          const roll = Math.random();
          if (roll < 0.8) {
            particles.push({ x, y, vx: (Math.random() - 0.5) * 0.4, vy: -1.6 - Math.random() * 2.2, life: 0, max: 14 + Math.random() * 18, size: 3 + Math.random() * 5, kind: "flame" });
          } else if (roll < 0.95) {
            particles.push({ x, y, vx: (Math.random() - 0.5) * 1.6, vy: -1.5 - Math.random() * 2.5, life: 0, max: 30 + Math.random() * 40, size: 1.2 + Math.random(), kind: "ember" });
          } else {
            particles.push({ x, y: y - 10, vx: (Math.random() - 0.5) * 0.5, vy: -0.6 - Math.random() * 0.6, life: 0, max: 50 + Math.random() * 30, size: 5 + Math.random() * 6, kind: "smoke" });
          }
        }
      }

      for (let k = particles.length - 1; k >= 0; k--) {
        const p = particles[k];
        p.life += 1;
        p.x += p.vx + Math.sin((p.life + p.y) * 0.2) * 0.3;
        p.y += p.vy;
        const age = p.life / p.max;
        if (age >= 1) {
          particles.splice(k, 1);
          continue;
        }
        context.beginPath();
        if (p.kind === "flame") {
          const radius = p.size * (1 - age * 0.6);
          const glow = context.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius);
          glow.addColorStop(0, `hsla(${52 - age * 40},100%,${78 - age * 30}%,${0.95 * (1 - age)})`);
          glow.addColorStop(1, `hsla(${30 - age * 25},100%,45%,0)`);
          context.fillStyle = glow;
          context.arc(p.x, p.y, radius, 0, Math.PI * 2);
        } else if (p.kind === "ember") {
          context.fillStyle = `hsla(${40 - age * 20},100%,60%,${1 - age})`;
          context.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        } else {
          context.fillStyle = `rgba(90,85,80,${0.07 * (1 - age)})`;
          context.arc(p.x, p.y, p.size * (1 + age), 0, Math.PI * 2);
        }
        context.fill();
      }

      if (burning || particles.length > 0) {
        requestAnimationFrame(frame);
      } else {
        canvas.remove();
        resolve();
      }
    };
    requestAnimationFrame(frame);
  });
}
