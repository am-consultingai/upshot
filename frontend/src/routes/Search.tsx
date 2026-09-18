import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useI18n } from "../i18n";

export default function SearchPage() {
  const { t } = useI18n();
  const [term, setTerm] = useState("");
  const results = useQuery({
    queryKey: ["search", term],
    queryFn: () => api.meetings({ q: term }),
    enabled: term.length > 1,
  });

  return (
    <section data-testid="search-page">
      <input
        data-testid="search-input"
        value={term}
        placeholder={t("search.placeholder")}
        onChange={(event) => setTerm(event.target.value)}
        className="w-full rounded border border-line px-3 py-2"
      />
      <h2 className="mt-4 text-sm font-semibold text-tertiary">{t("search.results")}</h2>
      <ul data-testid="search-results">
        {(results.data?.meetings ?? []).map((meeting) => (
          <li key={meeting.id} data-testid="search-result">
            <Link to={`/m/${meeting.id}`}>{meeting.title ?? meeting.id}</Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
