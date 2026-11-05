import { defineConfig } from "vite";

/** 개발 중에는 /api 호출을 FastAPI로 보내고, 빌드 결과는 FastAPI가 함께 제공합니다. */
export default defineConfig({
  server: {
    // 개발용 미리보기 호스트만 명시적으로 허용합니다. 전체 호스트 허용은 사용하지 않습니다.
    allowedHosts: ["terminal.local"],
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.js"],
  },
});
