"""사용자 설정(자본금·한도·업종별 비중). config.yaml 은 기본값, 화면에서 바꾼 값은 DB 가 덮어쓴다.

안전 규칙: 실주문 모드가 켜져 있는 동안 **한도를 느슨하게** 바꾸는 변경(자본·한도·리스크 증가, 업종 한도 상향/해제)은
확인 문구 없이는 저장하지 않는다. 조이는 변경은 항상 허용한다.
"""
import json

from . import db as dbm

KEY = "trading_overrides"
CONFIRM = "위험설정변경"
# key: (타입, 최소, 최대, 한글명, 단위)
FIELDS = {
    "capital_krw": ("int", 0, 10**12, "자본금", "원"),
    "daily_buy_limit_krw": ("int", 0, 10**12, "일일 매수 한도", "원"),
    "max_order_krw": ("int", 0, 10**12, "1회 최대 주문금액(0=제한 없음)", "원"),
    "risk_per_trade_pct": ("float", 0.1, 5.0, "종목당 손절 시 손실 한도", "% (자본 대비)"),
    "monthly_kill_pct": ("float", 1.0, 30.0, "월 손실 자동 중단선", "% (자본 대비)"),
    "max_positions": ("int", 1, 20, "최대 보유 종목 수", "개"),
    "max_group_pct": ("float", 5.0, 100.0, "업종(대분류) 기본 비중 한도", "% (자본 대비)"),
}


def get_overrides(database: dbm.Database | None = None) -> dict:
    d = database or dbm.get()
    r = d.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    try:
        raw = json.loads(r["value"]) if r else {}
    except ValueError:
        raw = {}
    return {k: v for k, v in raw.items() if k in FIELDS}


def sector_limits(database: dbm.Database | None = None) -> dict[str, float]:
    d = database or dbm.get()
    return {r["sector_id"]: float(r["max_pct"]) for r in d.execute("SELECT sector_id, max_pct FROM sector_limits")}


def parse(values: dict) -> dict:
    """입력 검증/정규화. 허용된 키만, 타입·범위 확인. 잘못되면 ValueError(한글 메시지)."""
    out = {}
    for k, v in values.items():
        if k not in FIELDS:
            raise ValueError(f"알 수 없는 설정: {k}")
        typ, lo, hi, label, _ = FIELDS[k]
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: 숫자를 입력하세요")
        if n != n or n in (float("inf"), float("-inf")) or not (lo <= n <= hi):
            raise ValueError(f"{label}: {lo:g} ~ {hi:g} 범위여야 합니다")
        if typ == "int":
            if n != int(n):
                raise ValueError(f"{label}: 정수여야 합니다")
            n = int(n)
        out[k] = n
    return out


def _looser(key: str, old: float, new: float) -> bool:
    if key == "max_order_krw":                     # 0 = 제한 없음 → 가장 느슨한 값
        eff = lambda x: float("inf") if x == 0 else x
        return eff(new) > eff(old)
    return new > old                               # 나머지는 값이 클수록 느슨(자본·한도·리스크%·보유종목수·업종한도)


def loosening(old_eff: dict, new_vals: dict) -> list[str]:
    return [FIELDS[k][3] for k, v in new_vals.items() if k in old_eff and _looser(k, old_eff[k], v)]


def save(effective_now: dict, values: dict, live_on: bool, confirm: str | None, database: dbm.Database | None = None) -> dict:
    """values 를 검증해 저장. effective_now: 변경 전 실제 적용 값(기본값+기존 덮어쓰기). 반환: 변경 내역."""
    d = database or dbm.get()
    clean = parse(values)
    changes = {k: [effective_now.get(k), v] for k, v in clean.items() if effective_now.get(k) != v}
    loose = loosening(effective_now, clean)
    if live_on and loose and confirm != CONFIRM:
        raise PermissionError(f"실주문 모드에서 한도를 느슨하게 바꾸려면 확인이 필요해요 ({', '.join(loose)})")
    merged = {**get_overrides(d), **clean}
    d.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (KEY, json.dumps(merged)))
    return changes


def set_sector_limit(sector_id: str, pct: float | None, default_pct: float, live_on: bool, confirm: str | None,
                     database: dbm.Database | None = None) -> dict:
    """업종(대/중분류) 한도 설정. pct=None 이면 개별 한도 해제(대분류는 기본 한도로 복귀, 중분류는 한도 없음)."""
    d = database or dbm.get()
    sec = d.execute("SELECT depth, name FROM sectors WHERE sector_id=?", (sector_id,)).fetchone()
    if not sec or sec["depth"] > 1:
        raise ValueError("대분류·중분류만 한도를 정할 수 있어요")
    if pct is not None:
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            raise ValueError("숫자를 입력하세요")
        if not (1 <= pct <= 100):
            raise ValueError("업종 한도는 1~100% 범위여야 해요")
    cur = sector_limits(d).get(sector_id)
    inf = float("inf")
    base = default_pct if sec["depth"] == 0 else inf                     # 개별 설정이 없을 때 적용되는 한도
    before = cur if cur is not None else base
    after = pct if pct is not None else base
    if live_on and after > before and confirm != CONFIRM:
        raise PermissionError(f"실주문 모드에서 '{sec['name']}' 한도를 느슨하게 바꾸려면 확인이 필요해요")
    if pct is None:
        d.execute("DELETE FROM sector_limits WHERE sector_id=?", (sector_id,))
    else:
        d.execute("INSERT INTO sector_limits(sector_id,max_pct) VALUES(?,?) ON CONFLICT(sector_id) DO UPDATE SET max_pct=EXCLUDED.max_pct", (sector_id, pct))
    return {"sector": sec["name"], "before": None if before == inf else before, "after": None if after == inf else after}


def reset(database: dbm.Database | None = None):
    d = database or dbm.get()
    d.execute("DELETE FROM settings WHERE key=?", (KEY,)); d.execute("DELETE FROM sector_limits")
