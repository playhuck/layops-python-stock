"""FCM(HTTP v1) 푸시 발송. 서비스 계정 JSON 경로를 .env 의 FCM_SERVICE_ACCOUNT 로 지정하면 활성화, 없으면 조용히 비활성.
알림에는 종목명/방향/건수만 싣고 금액·수량·계좌 정보는 싣지 않는다 (구글 FCM 서버를 경유하므로)."""
import json
import threading
from pathlib import Path

import requests

from ..envfile import env

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_cred = None
_lock = threading.Lock()


def configured() -> bool:
    try:
        return Path(env("FCM_SERVICE_ACCOUNT")).is_file()
    except KeyError:
        return False


def problem() -> str | None:
    """발송이 불가능한 이유(없으면 None). 화면에 그대로 보여준다. 경로만 노출하고 파일 내용은 읽지 않는다."""
    try:
        path = env("FCM_SERVICE_ACCOUNT")
    except KeyError:
        return ".env 에 FCM_SERVICE_ACCOUNT 가 없습니다"
    return None if Path(path).is_file() else f"서비스 계정 키 파일을 찾을 수 없습니다: {path}"


def _auth() -> tuple[str, str]:
    """(access_token, project_id). 토큰은 라이브러리가 만료 시 갱신한다."""
    global _cred
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    with _lock:
        if _cred is None:
            _cred = service_account.Credentials.from_service_account_file(env("FCM_SERVICE_ACCOUNT"), scopes=[SCOPE])
        if not _cred.valid:
            _cred.refresh(Request())
        return _cred.token, _cred.project_id


def send(store, title: str, body: str) -> dict:
    """등록된 모든 기기로 발송. 만료/해지된 토큰은 자동 삭제. 예외를 던지지 않는다(알림 실패가 매매 로직을 막으면 안 됨)."""
    res = {"sent": 0, "failed": 0, "error": None}
    if not configured():
        res["error"] = "FCM_SERVICE_ACCOUNT 미설정"
        return res
    try:
        token, project = _auth()
        for row in store.db.execute("SELECT token FROM push_tokens").fetchall():
            r = requests.post(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send", timeout=10,
                              headers={"Authorization": f"Bearer {token}"},
                              json={"message": {"token": row["token"], "notification": {"title": title, "body": body}}})
            if r.status_code == 200:
                res["sent"] += 1
            else:
                res["failed"] += 1
                if r.status_code in (400, 404) and any(k in r.text for k in ("UNREGISTERED", "INVALID_ARGUMENT")):
                    store.db.execute("DELETE FROM push_tokens WHERE token=?", (row["token"],))
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return res


def notify_async(store, title: str, body: str):
    threading.Thread(target=send, args=(store, title, body), daemon=True).start()
