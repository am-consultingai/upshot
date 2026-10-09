import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Icon } from "./Icon";

export type TimeOption = { value: string; at: Date; label?: string };

/**
 * A time the user types freely, with a list of suggestions under it.
 *
 * Not a `<datalist>`: Chromium filters a datalist by the text already in the box, so a box
 * holding "14:00" offered only "14:00". Here the list always holds every suggestion, and
 * opens scrolled to the one nearest the typed time. Typing never filters it; it closes
 * the list, and a click on the box, the arrow or ArrowDown brings it back.
 */
export default function TimeField({
  value,
  onChange,
  options,
  current,
  label,
  testId,
  className,
}: {
  value: string;
  onChange: (text: string) => void;
  options: TimeOption[];
  /** The typed time, parsed; null while it is not a time. */
  current: Date | null;
  label: string;
  testId: string;
  className: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);
  const listId = useId();

  const nearest = () => {
    if (!current) return 0;
    const at = options.findIndex((option) => option.at.getTime() >= current.getTime());
    return at === -1 ? options.length - 1 : at;
  };

  const show = () => {
    setActive(nearest());
    setOpen(true);
  };

  // Keep the highlighted suggestion in view, centred when the list opens.
  useEffect(() => {
    const list = listRef.current;
    const item = list?.children[active] as HTMLElement | undefined;
    if (!list || !item) return;
    const top = item.offsetTop - list.clientHeight / 2 + item.offsetHeight / 2;
    list.scrollTop = Math.max(0, top);
  }, [open, active]);

  const pick = (option: TimeOption) => {
    onChange(option.value);
    setOpen(false);
    inputRef.current?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) return show();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((index) => Math.min(options.length - 1, Math.max(0, index + step)));
    } else if (event.key === "Enter" && open && options[active]) {
      event.preventDefault();
      pick(options[active]);
    } else if (event.key === "Escape" && open) {
      // Closes the list only, not the dialog around it.
      event.preventDefault();
      event.nativeEvent.stopPropagation();
      setOpen(false);
    }
  };

  return (
    <div className="relative">
      <input
        ref={inputRef}
        data-testid={testId}
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="none"
        autoComplete="off"
        value={value}
        onChange={(event) => {
          // Typing is the user's own time: the list steps aside rather than covering Done.
          onChange(event.target.value);
          setOpen(false);
        }}
        onClick={() => (open ? undefined : show())}
        onKeyDown={onKeyDown}
        onBlur={() => setOpen(false)}
        className={`${className} pe-7`}
      />
      <button
        type="button"
        tabIndex={-1}
        aria-hidden="true"
        data-testid={`${testId}-toggle`}
        // Keeps the focus in the box, so the list does not close before it opens.
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          inputRef.current?.focus();
          if (open) setOpen(false);
          else show();
        }}
        className="absolute inset-y-0 end-0 grid w-7 place-items-center text-tertiary hover:text-primary"
      >
        <Icon icon={ChevronDown} className="size-3.5" />
      </button>
      {open && options.length > 0 && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          data-testid={`${testId}-options`}
          className="absolute inset-x-0 top-full z-10 mt-1 max-h-56 overflow-auto rounded-md bg-raised py-1 shadow-[var(--shadow-ring),var(--shadow-lg)]"
        >
          {options.map((option, index) => (
            <li
              key={option.at.getTime()}
              role="option"
              aria-selected={index === active}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(option)}
              className={`flex cursor-default justify-between gap-2 px-2 py-1 text-sm text-primary ${
                index === active ? "bg-a-200" : ""
              }`}
            >
              <span>{option.value}</span>
              {option.label && <span className="text-xs text-tertiary">{option.label}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
