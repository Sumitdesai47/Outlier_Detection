import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  base: "/plant-analysis/",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "../static/plant_analysis",
    emptyOutDir: true,
  },
  optimizeDeps: {
    include: ["plotly.js-dist-min", "react-plotly.js"],
  },
});
