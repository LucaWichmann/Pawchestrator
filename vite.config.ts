import { defineConfig } from "vite";
import monkey from "vite-plugin-monkey";

const rawUserscriptUrl =
  "https://raw.githubusercontent.com/LucaWichmann/Pawchestrator/main/dist/pawchestrator.user.js";

export default defineConfig({
  build: {
    minify: false,
    sourcemap: false,
  },
  plugins: [
    monkey({
      entry: "src/main.ts",
      userscript: {
        name: "Pawchestrator",
        namespace: "https://github.com/LucaWichmann/Pawchestrator",
        version: "0.1.0",
        description: "Agent orchestration controls for GitHub issues",
        match: ["https://github.com/*"],
        "run-at": "document-idle",
        grant: [
          "GM_addStyle",
          "GM_deleteValue",
          "GM_getValue",
          "GM_setValue",
          "GM_xmlhttpRequest",
        ],
        connect: ["127.0.0.1"],
        downloadURL: rawUserscriptUrl,
        updateURL: rawUserscriptUrl,
      },
      build: {
        fileName: "pawchestrator.user.js",
      },
    }),
  ],
});
