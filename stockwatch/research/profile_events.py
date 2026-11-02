"""매물대 연구용 사건(event) 결과 계산. 결정일 i 의 종가까지의 정보로 신호를 만들고, 체결은 다음 봉(i+1) 시가라는 기존 약속을 따른다.
일봉 안의 순서는 알 수 없으므로 같은 봉에서 손절가와 목표가가 모두 닿으면 보수(손절 먼저)와 낙관(목표 먼저)을 둘 다 돌려준다."""
from __future__ import annotations

import numpy as np


def forward_outcomes(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, i: int, atr: float, entry: float,
                     stop_k: float = 2.0, target_k: float = 3.5, horizons: tuple[int, ...] = (5, 10)) -> dict | None:
    """결정일 i 이후 체결(i+1 시가, 체결가 entry)한 포지션의 최대 유리·불리 이동폭(ATR 단위), 종가, 손절/목표 선도달.
    앞으로 볼 봉이 모자라면 None (미완결 표본은 쓰지 않는다)."""
    H = max(horizons)
    if i + H >= len(c) or not atr > 0:
        return None
    out: dict = {}
    for hz in horizons:
        sl = slice(i + 1, i + hz + 1)
        out[f"mfe{hz}"] = float((h[sl].max() - entry) / atr)
        out[f"mae{hz}"] = float((entry - l[sl].min()) / atr)
        out[f"close{hz}"] = float(c[i + hz])
    stop, target = entry - stop_k * atr, entry + target_k * atr
    first_cons = first_opt = "none"
    for j in range(i + 1, i + H + 1):
        hit_stop, hit_tgt = l[j] <= stop, h[j] >= target
        if hit_stop and hit_tgt:
            first_cons, first_opt = "stop", "target"
            break
        if hit_stop or hit_tgt:
            first_cons = first_opt = "stop" if hit_stop else "target"
            break
    out["first_cons"], out["first_opt"] = first_cons, first_opt
    return out


def zone_after(c: np.ndarray, i: int, lo: float, hi: float, reach_days: int = 10, resolve_days: int = 5) -> dict:
    """결정일 i 이후 종가 기준으로 저항 영역 [lo, hi] 에 처음 '진입'(종가 ≥ lo)한 날 j 와, 그 뒤 resolve_days 안의 결과.
    break=영역 상단 위로 종가 마감, reject=영역 하단 아래로 종가 마감(반락), stall=둘 다 아님, censored=뒤 봉이 모자람.
    종가만 쓰므로 일봉 안의 순서 문제가 없다. 진입한 날 종가가 이미 상단 위면 break."""
    n = len(c)
    for j in range(i + 1, min(i + reach_days, n - 1) + 1):
        if c[j] >= lo:
            if c[j] > hi:
                return {"reached": True, "day": j, "outcome": "break"}
            if j + resolve_days >= n:
                return {"reached": True, "day": j, "outcome": "censored"}
            for k in range(j + 1, j + resolve_days + 1):
                if c[k] > hi:
                    return {"reached": True, "day": j, "outcome": "break"}
                if c[k] < lo:
                    return {"reached": True, "day": j, "outcome": "reject"}
            return {"reached": True, "day": j, "outcome": "stall"}
    return {"reached": False, "day": None, "outcome": None}
