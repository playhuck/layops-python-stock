// Firebase 웹 앱 설정: 클라이언트에 공개되는 식별자라 비밀이 아니다 (보안은 Firebase 콘솔의 허용 도메인/규칙과 서버 측 서비스 계정이 담당).
export const firebaseConfig = {
  apiKey: "AIzaSyDgqJTeVS_QzTrlvOLiYFbBQVY10xnlPC0",
  authDomain: "layops-personal-fcm.firebaseapp.com",
  projectId: "layops-personal-fcm",
  storageBucket: "layops-personal-fcm.firebasestorage.app",
  messagingSenderId: "882079218925",
  appId: "1:882079218925:web:f6980fcdd90aefcd8de0e4",
};

// 웹 푸시 인증서의 공개 키. 루트 .env 의 FCM_KEY_PAIR 를 vite.config.ts 가 빌드 시 주입한다.
declare const __FCM_VAPID_KEY__: string;
export const vapidKey: string | undefined = __FCM_VAPID_KEY__ || undefined;
