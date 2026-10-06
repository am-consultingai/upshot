import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";
import { SELECT_CLASS } from "./SettingRow";
import type { UploadOptions } from "../lib/transcriptions";

/**
 * Where files come in: dropped anywhere on the zone, or chosen with "Add files". Several
 * at once queue in the order given. The options apply to every file added with them.
 */
export default function TranscriptionDropzone({
  options,
  onOptions,
  onFiles,
}: {
  options: UploadOptions;
  onOptions: (next: UploadOptions) => void;
  onFiles: (files: File[]) => void;
}) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);
  const languages = useQuery({ queryKey: ["languages"], queryFn: api.languages });

  return (
    <section className="mb-6 space-y-3">
      <div
        data-testid="transcription-dropzone"
        data-over={over || undefined}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          const files = Array.from(event.dataTransfer.files);
          if (files.length) onFiles(files);
        }}
        className={`flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-8 text-center ${
          over ? "border-accent bg-a-200" : "border-line-strong bg-raised"
        }`}
      >
        <p className="text-sm text-secondary">{t("transcriptions.drop")}</p>
        <button
          type="button"
          data-testid="transcription-add"
          onClick={() => input.current?.click()}
          className="h-8 rounded-md bg-accent px-3 text-sm font-medium text-on-accent hover:bg-accent-hover"
        >
          {t("transcriptions.add")}
        </button>
        <input
          ref={input}
          data-testid="transcription-file"
          type="file"
          multiple
          accept="audio/*,video/*"
          className="hidden"
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            event.target.value = "";
            if (files.length) onFiles(files);
          }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-secondary">{t("transcriptions.language")}</span>
          <select
            data-testid="transcription-language"
            value={options.language}
            onChange={(event) => onOptions({ ...options, language: event.target.value })}
            className={SELECT_CLASS}
          >
            <option value="auto">{t("transcriptions.languageAuto")}</option>
            {(languages.data?.languages ?? []).map((language) => (
              <option key={language.code} value={language.code}>
                {language.native}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <input
            data-testid="transcription-diarize"
            type="checkbox"
            checked={options.diarize}
            onChange={(event) => onOptions({ ...options, diarize: event.target.checked })}
            className="size-4 accent-[var(--accent)]"
          />
          <span>{t("transcriptions.diarize")}</span>
        </label>
        <label className="flex min-w-[16rem] flex-1 items-center gap-2">
          <span className="shrink-0 text-secondary">{t("transcriptions.prompt")}</span>
          <input
            data-testid="transcription-prompt"
            type="text"
            maxLength={1000}
            value={options.prompt}
            placeholder={t("transcriptions.promptHint")}
            onChange={(event) => onOptions({ ...options, prompt: event.target.value })}
            className="h-8 min-w-0 flex-1 rounded-md bg-surface-2 px-2.5 text-sm"
          />
        </label>
      </div>
    </section>
  );
}
