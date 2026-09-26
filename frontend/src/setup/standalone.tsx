import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "../index.css";
import { I18nContext, applyLocale, catalogues, type Locale, type MessageKey } from "../i18n";
import { applyTheme, type Theme } from "../theme";
import MockSetup from "./MockSetup";

/**
 * The setup mock as one HTML file that opens from disk (Setup 0, z8tj1hb03a).
 *
 * The real screens, the real copy and the scripted backend, with no app behind them:
 * the few calls the audio step makes are answered here, so the file can be opened by
 * someone who has never started Upshot. Built by `npm run build:mock`.
 */

const DEVICES = {
  devices: [
    { index: 1, name: "Microphone (Realtek Audio)", rate: 48000, channels: 2 },
    { index: 2, name: "Headset Microphone (Jabra Evolve2 65)", rate: 16000, channels: 1 },
  ],
  outputs: [
    { index: 3, name: "Speakers (Realtek Audio)", rate: 48000, channels: 2 },
    { index: 4, name: "Headset Earphone (Jabra Evolve2 65)", rate: 48000, channels: 2 },
  ],
  selected: null,
  selected_output: null,
  error: null,
  platform: "win32",
  capture: "wasapi",
  meter_opens: 0,
};

const config: Record<string, Record<string, unknown>> = { audio: { input_device: null, output_device: null } };

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input instanceof Request ? input.url : input);
  if (url.includes("/api/audio/devices")) return json(DEVICES);
  if (url.includes("/api/settings")) {
    if ((init?.method ?? "GET").toUpperCase() === "PUT") {
      const { values } = JSON.parse(String(init?.body ?? "{}")) as { values: Record<string, unknown> };
      for (const [key, value] of Object.entries(values)) {
        const [section, name] = key.split(".");
        config[section] = { ...config[section], [name]: value };
      }
    }
    return json({ config, pinned: {} });
  }
  return json({});
};

/** A meter that moves like someone talking, instead of the device's live level. */
class FakeLevels {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private timer = window.setInterval(() => {
    const rms = Math.max(0, 0.25 + 0.2 * Math.sin(Date.now() / 300) + 0.15 * Math.random());
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ rms, peak: Math.min(1, rms + 0.1) }) }));
  }, 120);
  close() {
    window.clearInterval(this.timer);
  }
  addEventListener() {}
}
(window as unknown as { EventSource: unknown }).EventSource = FakeLevels;

function Root() {
  const [locale, setLocale] = useState<Locale>("en");
  const [theme, setTheme] = useState<Theme>(
    window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  );
  useEffect(() => applyLocale(locale), [locale]);
  useEffect(() => applyTheme(theme), [theme]);
  const t = useCallback((key: MessageKey) => catalogues[locale][key], [locale]);
  const value = useMemo(
    () => ({ locale, t, setLocale, theme, setTheme, tooltips: true, setTooltips: () => undefined }),
    [locale, t, theme],
  );
  return (
    <I18nContext.Provider value={value}>
      <div className="flex h-screen bg-canvas text-primary">
        <MockSetup />
      </div>
    </I18nContext.Provider>
  );
}

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/${window.location.search}`]}>
        <Root />
      </MemoryRouter>
    </QueryClientProvider>
  </StrictMode>,
);
