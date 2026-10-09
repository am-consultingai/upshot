import type { LucideIcon, LucideProps } from "lucide-react";

/**
 * Every glyph in the app, from one set: Lucide (D94). The hand-drawn 16px paths each
 * had their own stroke and corner; one set reads as one product.
 *
 * The defaults live here so no call site restates them:
 * - Size: 16px, from `.ma-icon` in index.css. It sits in the components layer, so a
 *   `size-3` on the call site still wins.
 * - Stroke: `ICON_STROKE` screen pixels at every size (a non-scaling stroke), which is
 *   the weight the old 16px icons drew at. A 10px tick and a 20px empty-state glyph
 *   carry the same line, where a scaled stroke went hairline at the small end.
 * - Direction: `mirror` flips the glyph under a right-to-left direction. Only glyphs
 *   that point along the reading line take it (back and next, send); a play triangle,
 *   a clock or a vertical chevron does not turn in Hebrew.
 * - Decorative unless labelled: without an `aria-label` the icon is
 *   `aria-hidden`, and the control around it carries the name.
 */
export const ICON_STROKE = 1.5;

export type IconProps = Omit<LucideProps, "ref"> & {
  icon: LucideIcon;
  /** Point the other way in a right-to-left direction. */
  mirror?: boolean;
};

export function Icon({ icon: Glyph, mirror = false, className = "", strokeWidth = ICON_STROKE, ...rest }: IconProps) {
  const labelled = rest["aria-label"] !== undefined || rest["aria-labelledby"] !== undefined;
  return (
    <Glyph
      aria-hidden={labelled ? undefined : true}
      {...rest}
      strokeWidth={strokeWidth}
      nonScalingStroke
      className={`ma-icon${mirror ? " rtl:-scale-x-100" : ""}${className ? ` ${className}` : ""}`}
    />
  );
}
