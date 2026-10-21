import { defineConfig } from "vite";
import openapi from "./openapi.json" with { type: "json" };

const prefixes = new Set(Object.keys(openapi.paths).map((path) => `/${path.split("/")[1]}`));

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/" : "/",
  build: { outDir: "../cfl/web/dist", emptyOutDir: true, rolldownOptions: { input: ["index.html", "privacy.html"] } },
  // No changeOrigin: the API's same-site check compares Origin with the browser's Host.
  server: { proxy: Object.fromEntries([...prefixes].map((prefix) => [prefix, "http://127.0.0.1:8000"])) },
}));
