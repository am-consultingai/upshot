import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { interceptExternalLinks } from "./lib/external";
import { installErrorHandlers } from "./lib/errors";
import "./index.css";

// Links leaving the app open in the user's own browser, not Upshot's window profile.
interceptExternalLinks();
// Errors nobody caught go to the local server, which reports them only with consent (D87).
installErrorHandlers();

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
