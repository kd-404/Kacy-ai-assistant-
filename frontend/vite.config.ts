import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base matches the GitHub Pages project-site path: https://kd-404.github.io/kacy-ai-assistant-/
export default defineConfig({
  base: "/kacy-ai-assistant-/",
  plugins: [react()],
  server: { port: 5173 },
});
