import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/**
 * The setup mock as a single HTML file (Setup 0). Everything — script, styles, fonts —
 * is inlined so the file opens straight from disk; `scripts/inline-mock.mjs` folds the
 * one script and one stylesheet this produces into the page. Never part of the app build.
 */
export default defineConfig({
  // The root stays the frontend folder: Tailwind finds the classes to generate by
  // scanning from the root, and rooted at mock/ it saw none of the components.
  base: "./",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "dist-mock",
    emptyOutDir: true,
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      input: resolve(__dirname, "mock/index.html"),
      output: { inlineDynamicImports: true },
    },
  },
});
