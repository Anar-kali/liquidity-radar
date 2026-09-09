import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// publicDir defaults to "public", so export_site.py's output at
// site/public/data/* is served at /data/* in dev and copied into dist on build.
export default defineConfig({
  plugins: [react()],
  server: { port: 5174 },
});
