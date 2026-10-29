"""엔진 싱글톤 접근 (웹 API 와 갱신 파이프라인이 같은 Store/Gateway/락을 공유한다).
토스 토큰은 client 당 1개라 Gateway(=TossClient)도 프로세스에 하나만 둔다."""
from .. import config
from ..envfile import ROOT
from ..providers import DbProvider
from .. import sectors
from . import push
from .engine import Engine, Gateway
from .store import Store

_store = _gateway = None


def _cfg() -> dict:
    return config.load()


def engine() -> Engine:
    global _store, _gateway
    if _store is None:
        _store, _gateway = Store(), Gateway()
    cfg = _cfg()  # 설정 파일 변경을 재시작 없이 반영
    return Engine(_store, _gateway, cfg, DbProvider(),
                  notifier=lambda title, body: push.notify_async(_store, title, body))


def after_refresh() -> None:
    """갱신 직후: 체결 동기화(→ 보호주문/원장) → 제안 생성."""
    e = engine()
    if e.gw.configured():
        try:
            sectors.ensure_fresh(e.gw.client, _store.db)
        except Exception as ex:  # 업종 동기화 실패가 체결 동기화/제안 생성을 막으면 안 된다
            _store.audit("sector_sync_failed", {"error": f"{type(ex).__name__}: {str(ex)[:120]}"})
    try:
        e.sync_orders()
    finally:
        e.generate()
