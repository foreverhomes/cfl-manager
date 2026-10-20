import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/",
  build: { outDir: "../cfl/web/dist", emptyOutDir: true, rolldownOptions: { input: ["index.html", "privacy.html"] } },
});
