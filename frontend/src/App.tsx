import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { I18nContext, applyLocale, catalogues, type Locale, type MessageKey } from "./i18n";
import Timeline from "./routes/Timeline";
import MeetingPage from "./routes/Meeting";
import SearchPage from "./routes/Search";
import Attention from "./routes/Attention";
import Glossary from "./routes/Glossary";
import Settings from "./routes/Settings";
import Detector from "./routes/Detector";

const NAV: { to: string; key: MessageKey; testid: string }[] = [
  { to: "/", key: "nav.timeline", testid: "nav-timeline" },
  { to: "/search", key: "nav.search", testid: "nav-search" },
  { to: "/attention", key: "nav.attention", testid: "nav-attention" },
  { to: "/glossary", key: "nav.glossary", testid: "nav-glossary" },
  { to: "/detector", key: "nav.detector", testid: "nav-detector" },
  { to: "/settings", key: "nav.settings", testid: "nav-settings" },
];

export default function App() {
  const [locale, setLocale] = useState<Locale>("en");
  const queryClient = useQueryClient();

  useEffect(() => {
    applyLocale(locale);
  }, [locale]);

  useEffect(() => {
    const source = new EventSource("/api/events");
    const invalidate = () => {
      void queryClient.invalidateQueries();
    };
    source.addEventListener("job", invalidate);
    source.addEventListener("recorder", invalidate);
    source.addEventListener("meeting", invalidate);
    return () => source.close();
  }, [queryClient]);

  const t = useCallback((key: MessageKey) => catalogues[locale][key], [locale]);
  const value = useMemo(() => ({ locale, t, setLocale }), [locale, t]);

  return (
    <I18nContext.Provider value={value}>
      <div className="min-h-screen bg-neutral-50 text-neutral-900" data-testid="app">
        <header className="border-b border-neutral-200 bg-white">
          <nav className="mx-auto flex max-w-5xl items-center gap-4 px-4 py-3">
            <span className="font-semibold" data-testid="app-title">
              {t("app.title")}
            </span>
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                data-testid={item.testid}
                className={({ isActive }) =>
                  isActive ? "text-sm font-semibold underline" : "text-sm text-neutral-600"
                }
              >
                {t(item.key)}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6">
          <Routes>
            <Route path="/" element={<Timeline />} />
            <Route path="/m/:id" element={<MeetingPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/attention" element={<Attention />} />
            <Route path="/glossary" element={<Glossary />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/detector" element={<Detector />} />
          </Routes>
        </main>
      </div>
    </I18nContext.Provider>
  );
}
