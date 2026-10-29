// FCM 백그라운드 알림 서비스워커 (페이지가 닫혀 있어도 알림 표시). 설정값은 공개 식별자.
importScripts("https://www.gstatic.com/firebasejs/12.0.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/12.0.0/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyDgqJTeVS_QzTrlvOLiYFbBQVY10xnlPC0",
  authDomain: "layops-personal-fcm.firebaseapp.com",
  projectId: "layops-personal-fcm",
  storageBucket: "layops-personal-fcm.firebasestorage.app",
  messagingSenderId: "882079218925",
  appId: "1:882079218925:web:f6980fcdd90aefcd8de0e4",
});
// notification 페이로드가 포함된 메시지는 SDK 가 자동으로 표시한다 (onBackgroundMessage 불필요).
firebase.messaging();

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => (cs.length ? cs[0].focus() : self.clients.openWindow("/"))));
});
