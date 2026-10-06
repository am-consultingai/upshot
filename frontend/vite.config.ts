import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Source maps are written without the comment that would point a browser at them: the
  // official build moves them out before packaging and uploads them to Sentry for the
  // release (D87), so crash reports read as source while no map ships to users.
  build: { outDir: "dist", emptyOutDir: true, sourcemap: "hidden" },
  server: { port: 5173 },
});
