import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { label, quickChoices, searchLanguages } from "../lib/transcribeAgain";
import Button from "./Button";

/**
 * "Transcribe again as…" (R9): for the rare meeting whose language was picked wrong.
 * Reached only from the meeting's ⋯ menu; nothing else mentions it (R7). Hebrew and
 * the classifier's next guesses come first; "Other…" searches every Whisper language.
 */
export default function TranscribeAgainDialog({
  candidates,
  onChoose,
  onClose,
}: {
  candidates: string[] | undefined;
  onChoose: (code: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const languages = useQuery({ queryKey: ["languages"], queryFn: api.languages, staleTime: Infinity });
  const [other, setOther] = useState(false);
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement | null>(null);
  const all = languages.data?.languages ?? [];
  const quick = useMemo(() => quickChoices(candidates, all), [candidates, all]);
  const found = useMemo(() => searchLanguages(all, query), [all, query]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (other) search.current?.focus();
  }, [other]);

  const choice = (code: string, text: string) => (
    <button
      key={code}
      type="button"
      data-testid="transcribe-again-choice"
      data-code={code}
      onClick={() => onChoose(code)}
      className="flex h-7 w-full items-center rounded-md px-2 text-start text-sm text-primary hover:bg-a-200"
    >
      <span className="truncate">{text}</span>
    </button>
  );

  return createPortal(
    <div className="fixed inset-0 z-[80] grid place-items-center bg-scrim-soft px-4 backdrop-blur-xs">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="transcribe-again-title"
        data-testid="transcribe-again"
        className="ma-dialog w-full max-w-sm rounded-xl bg-raised p-4 shadow-[var(--shadow-ring),var(--shadow-lg),var(--shadow-edge)]"
      >
        <h2 id="transcribe-again-title" className="text-md font-semibold tracking-snug">
          {t("transcribeAgain.title")}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-secondary">{t("transcribeAgain.body")}</p>
        <div className="mt-3 flex flex-col gap-0.5">
          {quick.map((language) => choice(language.code, label(language)))}
          {!other && (
            <button
              type="button"
              data-testid="transcribe-again-other"
              onClick={() => setOther(true)}
              className="flex h-7 w-full items-center rounded-md px-2 text-start text-sm text-secondary hover:bg-a-200"
            >
              {t("transcribeAgain.other")}
            </button>
          )}
        </div>
        {other && (
          <div className="mt-2">
            <input
              ref={search}
              data-testid="transcribe-again-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("transcribeAgain.search")}
              className="h-8 w-full rounded-md bg-transparent px-2 text-sm text-primary shadow-[var(--shadow-ring)] outline-none placeholder:text-tertiary"
            />
            <div className="mt-1 max-h-56 overflow-y-auto">
              {found.map((language) => choice(language.code, label(language)))}
            </div>
          </div>
        )}
        <div className="mt-4 flex justify-end">
          <Button
            data-testid="transcribe-again-cancel"
            onClick={onClose}
            variant="ghost"
          >
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
