import { initializeApp } from "firebase/app";
import { getMessaging, getToken, isSupported, onMessage } from "firebase/messaging";
import { firebaseConfig, vapidKey } from "./firebaseConfig";

const app = initializeApp(firebaseConfig);

export interface PushResult { ok: boolean; message: string }

/** 알림 권한 요청(사용자 클릭 필요) → FCM 토큰 발급 → 서버에 등록 */
export async function enablePush(): Promise<PushResult> {
  if (!vapidKey) return { ok: false, message: "FCM_KEY_PAIR 미설정 (루트 .env 에 웹 푸시 공개 키를 넣고 npm run build)" };
  if (!(await isSupported())) return { ok: false, message: "이 브라우저/접속 환경은 웹 푸시를 지원하지 않습니다 (HTTPS 또는 localhost 필요)" };
  if ((await Notification.requestPermission()) !== "granted") return { ok: false, message: "알림 권한이 거부되었습니다" };
  const reg = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
  const token = await getToken(getMessaging(app), { vapidKey, serviceWorkerRegistration: reg });
  if (!token) return { ok: false, message: "토큰 발급 실패" };
  const r = await fetch("/api/push/register", { method: "POST", headers: { "Content-Type": "application/json", "X-Stockwatch": "1" }, body: JSON.stringify({ token }) });
  return r.ok ? { ok: true, message: "이 기기에서 푸시 알림을 켰습니다" } : { ok: false, message: "서버 등록 실패" };
}

/** 화면을 보고 있을 때 도착한 알림(포그라운드)은 브라우저가 자동 표시하지 않으므로 직접 표시 */
export function listenForeground(cb: (title: string, body: string) => void) {
  isSupported().then((ok) => {
    if (ok) onMessage(getMessaging(app), (m) => cb(m.notification?.title ?? "알림", m.notification?.body ?? ""));
  });
}
