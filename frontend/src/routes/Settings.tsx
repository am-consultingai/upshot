import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n, type Locale } from "../i18n";

/** Language endonyms are data, not copy: they are never translated. */
const LANGUAGE_NAMES: Record<string, string> = { en: "English", he: "עברית", auto: "auto" };

interface ConfigShape {
  data_root?: string | null;
  ui?: { language?: string };
  summary?: { language?: string };
}

export default function Settings() {
  const { t, locale, setLocale } = useI18n();
  const queryClient = useQueryClient();
  const [dataRoot, setDataRoot] = useState("");
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.putSettings(values),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const config = (settings.data?.config ?? {}) as ConfigShape;

  useEffect(() => {
    if (config.data_root !== undefined && config.data_root !== null) setDataRoot(config.data_root);
  }, [config.data_root]);

  const warnings = settings.data?.warnings ?? [];
  const localWarning = /onedrive|dropbox|google drive/i.test(dataRoot);

  return (
    <section data-testid="settings-page">
      <label className="mb-1 block text-sm font-medium" htmlFor="data-root">
        {t("settings.dataRoot")}
      </label>
      <input
        id="data-root"
        data-testid="data-root"
        value={dataRoot}
        onChange={(event) => setDataRoot(event.target.value)}
        className="w-full rounded border border-neutral-300 px-2 py-1"
      />
      {(localWarning || warnings.length > 0) && (
        <p data-testid="sync-warning" className="mt-1 text-sm text-amber-700">
          {t("settings.syncWarning")}
        </p>
      )}

      <label className="mt-4 mb-1 block text-sm font-medium" htmlFor="ui-language">
        {t("settings.language")}
      </label>
      <select
        id="ui-language"
        data-testid="ui-language"
        value={locale}
        onChange={(event) => {
          const next = event.target.value as Locale;
          setLocale(next);
          save.mutate({ "ui.language": next });
        }}
        className="rounded border border-neutral-300 px-2 py-1"
      >
        {["en", "he"].map((code) => (
          <option key={code} value={code}>
            {LANGUAGE_NAMES[code]}
          </option>
        ))}
      </select>

      <label className="mt-4 mb-1 block text-sm font-medium" htmlFor="summary-language">
        {t("settings.summaryLanguage")}
      </label>
      <select
        id="summary-language"
        data-testid="summary-language"
        value={config.summary?.language ?? "en"}
        onChange={(event) => save.mutate({ "summary.language": event.target.value })}
        className="rounded border border-neutral-300 px-2 py-1"
      >
        {["en", "he", "auto"].map((code) => (
          <option key={code} value={code}>
            {LANGUAGE_NAMES[code]}
          </option>
        ))}
      </select>

      <div className="mt-4">
        <button
          type="button"
          data-testid="settings-save"
          onClick={() => save.mutate({ data_root: dataRoot })}
          className="rounded bg-neutral-900 px-3 py-1.5 text-white"
        >
          {t("settings.save")}
        </button>
        {save.isSuccess && (
          <span data-testid="settings-saved" className="ms-2 text-sm text-green-700">
            {t("settings.saved")}
          </span>
        )}
      </div>
    </section>
  );
}
