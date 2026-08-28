import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useI18n } from "../i18n";

export default function Glossary() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [term, setTerm] = useState("");
  const [aliases, setAliases] = useState("");
  const glossary = useQuery({ queryKey: ["glossary"], queryFn: api.glossary });
  const add = useMutation({
    mutationFn: () => api.putGlossary([{ term, aliases }]),
    onSuccess: () => {
      setTerm("");
      setAliases("");
      void queryClient.invalidateQueries({ queryKey: ["glossary"] });
    },
  });

  return (
    <section data-testid="glossary-page">
      <form
        className="mb-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          add.mutate();
        }}
      >
        <input
          data-testid="glossary-term"
          value={term}
          placeholder={t("glossary.term")}
          onChange={(event) => setTerm(event.target.value)}
          className="rounded border border-neutral-300 px-2 py-1"
        />
        <input
          data-testid="glossary-aliases"
          value={aliases}
          placeholder={t("glossary.aliases")}
          onChange={(event) => setAliases(event.target.value)}
          className="rounded border border-neutral-300 px-2 py-1"
        />
        <button type="submit" data-testid="glossary-add" className="rounded bg-neutral-900 px-3 text-white">
          {t("glossary.add")}
        </button>
      </form>
      <ul data-testid="glossary-list">
        {(glossary.data?.terms ?? []).map((entry) => (
          <li key={entry.term} data-testid="glossary-row">
            <strong>{entry.term}</strong> {entry.aliases ?? ""}
          </li>
        ))}
      </ul>
    </section>
  );
}
