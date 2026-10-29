import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 루트 .env 의 FCM_KEY_PAIR(웹 푸시 VAPID *공개* 키)만 프런트에 주입한다. 다른 값(TOSS_*, ID/PW 등)은 절대 번들에 들어가지 않는다.
export default defineConfig(({ mode }) => {
  const raw = loadEnv(mode, "..", "FCM_").FCM_KEY_PAIR ?? "";
  // 공개 키 = 65바이트 P-256 점 → base64url 87자, 'B' 로 시작. 비공개 키(43자)를 실수로 넣으면 공개 JS 에 노출되므로 빌드를 거부한다.
  const looksPublic = /^B[A-Za-z0-9_-]{86}$/.test(raw);
  if (raw && !looksPublic) throw new Error("FCM_KEY_PAIR 가 웹 푸시 공개 키 형식(87자, B 로 시작)이 아닙니다. 비공개 키를 넣었다면 즉시 폐기/재발급하세요.");
  return {
    plugins: [react()],
    define: { __FCM_VAPID_KEY__: JSON.stringify(looksPublic ? raw : "") },
    server: { port: 5173, proxy: { "/api": "http://127.0.0.1:5000" } },
  };
});
