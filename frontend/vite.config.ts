import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { nitro } from "nitro/vite";

export default defineConfig({
  resolve: { tsconfigPaths: true },
  // Nitro compiles the server build for the deploy target. Vercel detects
  // TanStack Start + Nitro automatically and applies the `vercel` preset, so no
  // build command or output directory has to be set.
  plugins: [tanstackStart(), nitro(), viteReact(), tailwindcss()],
});
