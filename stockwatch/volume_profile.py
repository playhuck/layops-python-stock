"""일봉 범위 기반 추정 매물대(가격대별 거래량).

실제 체결 가격별 거래량이 아니라, 하루 거래량이 그날의 [저가, 고가] 안에 고르게 퍼졌다고 가정한 추정치다.
차트 표시·연구 스크립트·(향후) 매수 판단이 같은 함수를 쓰도록 순수 계산만 둔다 (DB·네트워크 없음).
입력은 '완성된 봉'만 받는다. 한국은 complete_kr_bars 로 걸러서 넣는다.
"""
from __future__ import annotations

import math
from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import indicators as ind

METHOD_VERSION = 1
WINDOW, BINS, THICK = 120, 24, 1.5       # 주 기준(고정). 다른 값은 연구의 민감도 확인에만 쓴다
MIN_BARS = 40                            # 유효 봉이 이보다 적으면 계산하지 않는다
KST = ZoneInfo("Asia/Seoul")
KR_CLOSED_AFTER = time(15, 40)           # 정규장 15:30 + 동시호가 종료 여유


def complete_kr_bars(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """한국 정규장 마감(KST 15:40) 전의 오늘 봉은 장중 값이라 제외한다. 미국 규칙(동부시간)과 따로 둔다."""
    if df.empty:
        return df
    now = (now or datetime.now(KST)).astimezone(KST)
    d = np.array(df.index.date)
    return df[(d < now.date()) | ((d == now.date()) & (now.time() >= KR_CLOSED_AFTER))]


def _f(x) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


def _fail(reason: str, **meta) -> dict:
    return {"ok": False, "reason": reason, "meta": {"method_version": METHOD_VERSION, **meta}}


def _allocate(lo: np.ndarray, hi: np.ndarray, vol: np.ndarray, close: np.ndarray, edges: np.ndarray, allocation: str) -> np.ndarray:
    """봉마다 거래량을 가격 구간에 배분해 구간별 합계(길이 bins)를 돌려준다."""
    n_bins = len(edges) - 1
    b_lo, b_hi = edges[:-1], edges[1:]
    if allocation == "close":                       # 민감도 비교용: 종가가 속한 구간에 전량
        idx = np.clip(np.searchsorted(edges, close, side="right") - 1, 0, n_bins - 1)
        return np.bincount(idx, weights=vol, minlength=n_bins).astype(float)
    if allocation != "range_overlap":
        raise ValueError(f"unknown allocation: {allocation}")
    out = np.zeros(n_bins)
    flat = hi <= lo                                 # 고가 == 저가: 그 가격이 속한 구간에 전량
    if flat.any():
        idx = np.clip(np.searchsorted(edges, lo[flat], side="right") - 1, 0, n_bins - 1)
        out += np.bincount(idx, weights=vol[flat], minlength=n_bins)
    r = ~flat
    if r.any():
        ov = np.clip(np.minimum(hi[r, None], b_hi[None, :]) - np.maximum(lo[r, None], b_lo[None, :]), 0, None)
        out += (ov / (hi[r] - lo[r])[:, None] * vol[r, None]).sum(axis=0)
    return out


def _zones(bins: list[dict], thick: float) -> list[dict]:
    """구간별로 평균의 thick 배 이상인 것만 골라, 인접한 것끼리 하나의 영역으로 합친다 (합친 뒤 재판정하지 않음)."""
    zones: list[dict] = []
    for i, b in enumerate(bins):
        if b["mult"] < thick:
            continue
        if zones and zones[-1]["_last"] == i - 1:
            z = zones[-1]
            z["hi"], z["volume"], z["share"], z["_last"], z["n_bins"] = b["hi"], z["volume"] + b["volume"], z["share"] + b["share"], i, z["n_bins"] + 1
            z["peak_mult"] = max(z["peak_mult"], b["mult"])
        else:
            zones.append({"lo": b["lo"], "hi": b["hi"], "volume": b["volume"], "share": b["share"], "peak_mult": b["mult"], "n_bins": 1, "_last": i})
    for z in zones:
        del z["_last"]
    return zones


def compute(df: pd.DataFrame, window: int = WINDOW, bins: int = BINS, thick: float = THICK, allocation: str = "range_overlap") -> dict:
    """df: 날짜 인덱스 + open/high/low/close/volume 의 '완성된 일봉'. 결과는 JSON 으로 그대로 내려도 되는 dict.
    ok=False 면 reason 에 계산 불가 사유가 있다 (이때 호출하는 쪽은 기존 판단을 바꾸지 않는다)."""
    meta0 = {"window": window, "bins": bins, "thickness_multiple": thick, "allocation": allocation}
    need = {"high", "low", "close", "volume"}
    if df is None or df.empty or not need <= set(df.columns):
        return _fail("거래 데이터 없음", **meta0)
    win = df.tail(window)
    h, l, c, v = (win[k].to_numpy(float) for k in ("high", "low", "close", "volume"))
    good = np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & np.isfinite(v) & (l > 0) & (h >= l) & (v > 0)
    used = int(good.sum())
    meta = {"method_version": METHOD_VERSION, **meta0, "last_date": win.index[-1].strftime("%Y-%m-%d"), "bars_used": used, "bars_excluded": int(len(win) - used)}
    if used < MIN_BARS:
        return {"ok": False, "reason": f"유효한 거래일 부족 ({used}일 < {MIN_BARS}일)", "meta": meta}
    if not good[-1]:
        return {"ok": False, "reason": "마지막 봉의 가격·거래량이 비정상", "meta": meta}
    h, l, c, v = h[good], l[good], c[good], v[good]
    price = float(c[-1])
    top, bottom = float(h.max()), float(l.min())
    if top <= bottom:
        return {"ok": False, "reason": "가격 범위가 없음", "meta": meta}
    edges = np.linspace(bottom, top, bins + 1)
    vols = _allocate(l, h, v, c, edges, allocation)
    total = float(vols.sum())
    mean = total / bins
    poc_i = int(vols.argmax())
    rows = [{"lo": float(edges[i]), "hi": float(edges[i + 1]), "volume": float(vols[i]), "share": float(vols[i] / total),
             "rel_poc": float(vols[i] / vols[poc_i]), "mult": float(vols[i] / mean)} for i in range(bins)]
    zones = _zones(rows, thick)

    a = ind.atr(df).iloc[-1]                                  # 윈도우가 아니라 전체 입력으로 계산 (Wilder 는 초기 구간이 필요)
    atr = _f(a) if _f(a) and a > 0 else None

    def reach(z: dict, edge: float) -> dict:                   # 거리는 현재가에서 해당 경계까지 (위: 영역 하단 진입, 상단 돌파 / 아래: 영역 상단 진입, 하단 이탈)
        return {"lo": z["lo"], "hi": z["hi"], "share": z["share"], "peak_mult": z["peak_mult"],
                "enter_pct": (edge - price) / price * 100, "enter_atr": (edge - price) / atr if atr else None}

    inside = next((z for z in zones if z["lo"] <= price <= z["hi"]), None)
    above = [z for z in zones if z["lo"] > price]
    below = [z for z in zones if z["hi"] < price]
    up = down = None
    if above:
        z = min(above, key=lambda z: z["lo"])
        up = {**reach(z, z["lo"]), "break_pct": (z["hi"] - price) / price * 100, "break_atr": (z["hi"] - price) / atr if atr else None}
    if below:
        z = max(below, key=lambda z: z["hi"])
        down = reach(z, z["hi"])
    return {"ok": True, "reason": None, "meta": meta, "price": price, "atr": atr, "total_volume": total,
            "bins": rows, "poc": {"lo": rows[poc_i]["lo"], "hi": rows[poc_i]["hi"], "share": rows[poc_i]["share"]},
            "zones": zones, "inside": inside and {"lo": inside["lo"], "hi": inside["hi"], "share": inside["share"], "peak_mult": inside["peak_mult"]},
            "up": up, "down": down}
