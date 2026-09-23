/**
 * The "!" beside something that needs setting up.
 *
 * Amber rather than red: nothing is broken yet, something is missing. It carries its
 * reason as a label and a tooltip, since a bare glyph says "look here" and not why.
 */
export default function SetupMark({ label, testid }: { label: string; testid?: string }) {
  return (
    <span
      data-testid={testid}
      role="img"
      aria-label={label}
      title={label}
      className="grid size-4 shrink-0 place-items-center rounded-full bg-warning text-[10px] leading-none font-bold text-on-solid"
    >
      !
    </span>
  );
}
