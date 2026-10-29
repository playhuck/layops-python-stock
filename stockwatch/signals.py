"""지표 → 매수/매도 점수 → 판정. 모든 규칙은 사용자가 제시한 스윙 기준을 그대로 옮긴 것이다.

점수 체계: 매수 점수(buy)와 매도 점수(sell)를 따로 누적하고, 근거(reasons)를 문자열로 남긴다.
임계값은 config.yaml 의 thresholds 로 조정 가능하게 두었다 (하드코딩 최소화).
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind

DEFAULTS = {
    "pullback_tol_pct": 2.0,     # 5/20일선까지 눌렸다고 보는 허용 오차(%)
    "rsi_oversold": 30, "rsi_overbought": 70,
    "rsi_lookback": 5,           # 과매도 이탈을 최근 몇 일 내로 볼지
    "stoch_low": 20, "stoch_high": 80, "stoch_lookback": 3,
    "flow_days": 7,
    "vix_calm": 15, "vix_caution": 25, "vix_fear": 30,
    "buy_threshold": 55, "split_buy_threshold": 40, "sell_threshold": 40,
}


@dataclass
class Result:
    code: str
    name: str
    price: float
    buy_score: int
    sell_score: int
    verdict: str
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _crossed_up(a: pd.Series, b: pd.Series, lookback: int) -> bool:
    diff = (a - b).tail(lookback + 1)
    return bool(len(diff) > 1 and ((diff.shift(1) < 0) & (diff >= 0)).any())


def _crossed_down(a: pd.Series, b: pd.Series, lookback: int) -> bool:
    diff = (a - b).tail(lookback + 1)
    return bool(len(diff) > 1 and ((diff.shift(1) > 0) & (diff <= 0)).any())


def vix_regime(vix: float | None, t: dict) -> tuple[str, int]:
    """VIX 구간 → (설명, 매수점수 보정). 높을수록 '지금 사도 되나' 에 보수적으로 반응한다."""
    if vix is None or np.isnan(vix):
        return "VIX 데이터 없음", 0
    if vix < t["vix_calm"]:
        return f"VIX {vix:.1f} 안정 구간: 추세추종 유리, 다만 과열 주의", 0
    if vix < t["vix_caution"]:
        return f"VIX {vix:.1f} 보통 구간", 0
    if vix < t["vix_fear"]:
        return f"VIX {vix:.1f} 경계 구간: 변동성 확대, 매수는 분할로", -10
    return f"VIX {vix:.1f} 공포 구간: 급락 변동 큼. 진입 시 소량 분할 + 손절 엄수", -15


def analyze(code: str, name: str, df: pd.DataFrame, flows: pd.DataFrame | None = None,
            vix: float | None = None, thresholds: dict | None = None) -> Result:
    t = {**DEFAULTS, **(thresholds or {})}
    if len(df) < 130:
        raise ValueError(f"{code}: 120일선 계산에는 최소 130일 데이터가 필요합니다 (현재 {len(df)}일)")

    d = ind.add_all(df)
    last, prev = d.iloc[-1], d.iloc[-2]
    price = float(last["close"])
    buy, sell, why, warn = 0, 0, [], []

    # ---- 1) 이동평균: 20일선 우상향 + 5/20일선 눌림목 (최대 +30) -------------------
    ma20_up = d["ma20"].iloc[-1] > d["ma20"].iloc[-6]
    near = lambda ma: abs(price / last[ma] - 1) * 100 <= t["pullback_tol_pct"]
    above = lambda ma: price > last[ma]
    if ma20_up:
        buy += 10; why.append("20일선 우상향")
        pulled = (price <= last["ma5"] * (1 + t["pullback_tol_pct"] / 100)) and near("ma5") or near("ma20")
        # 눌림목: 최근 고점 대비 내려왔고 5일선 또는 20일선 근처
        recent_high = d["close"].tail(10).max()
        if pulled and price < recent_high:
            buy += 20; why.append("눌림목: 20일선 우상향 상태에서 5/20일선 근처까지 조정")
    else:
        warn.append("20일선이 우상향이 아님 → 스윙 매수 조건 미충족")

    # ---- 2) 중장기 추세: 60/120일선 위 (최대 +15, 이탈 시 경고) -------------------
    if above("ma60") and above("ma120"):
        buy += 15; why.append("60/120일선 위 (중장기 추세 안전)")
    else:
        lost = [n for n in ("ma60", "ma120") if not above(n)]
        warn.append(f"{'/'.join(m[2:] + '일선' for m in lost)} 아래 → 중장기 추세 불안")
        sell += 10

    # ---- 3) RSI: 과매도 이탈 = 분할매수, 과매수 = 익절 준비 -------------------------
    r = d["rsi"]
    lb = t["rsi_lookback"]
    if (r.tail(lb + 1).iloc[:-1] <= t["rsi_oversold"]).any() and r.iloc[-1] > t["rsi_oversold"]:
        buy += 15; why.append(f"RSI 과매도({t['rsi_oversold']}) 이탈 → 분할 매수 구간")
    elif r.iloc[-1] <= t["rsi_oversold"]:
        warn.append(f"RSI {r.iloc[-1]:.0f} 과매도 진행 중: 이탈 확인 후 진입")
    if r.iloc[-1] >= t["rsi_overbought"]:
        sell += 20; why.append(f"RSI {r.iloc[-1]:.0f} 과매수 → 익절 준비")

    # ---- 4) 스토캐스틱 슬로우 ---------------------------------------------------------
    k, dd = d["stoch_k"], d["stoch_d"]
    if _crossed_up(k, dd, t["stoch_lookback"]) and k.tail(t["stoch_lookback"] + 2).min() <= t["stoch_low"] + 10:
        buy += 15; why.append("스토캐스틱 과매도권 골든크로스 (%K↑%D)")
    if _crossed_down(k, dd, t["stoch_lookback"]) and k.tail(t["stoch_lookback"] + 2).max() >= t["stoch_high"] - 10:
        sell += 20; why.append("스토캐스틱 과매수권 데드크로스 (%K↓%D)")

    # ---- 5) MACD ----------------------------------------------------------------------
    m, s, h = d["macd"], d["macd_signal"], d["macd_hist"]
    if _crossed_up(m, s, 2) or (prev["macd_hist"] < 0 <= last["macd_hist"]):
        buy += 10; why.append("MACD 골든크로스 / 히스토그램 음→양 전환")
    if _crossed_down(m, s, 2) or (prev["macd_hist"] > 0 >= last["macd_hist"]):
        sell += 10; why.append("MACD 데드크로스 / 히스토그램 양→음 전환")

    # ---- 6) 볼린저밴드 ----------------------------------------------------------------
    bullish_candle = last["close"] > last["open"]
    if last["low"] <= last["bb_low"] * 1.01 and bullish_candle:
        buy += 15; why.append("볼린저 하한선 지지 + 양봉")
    if last["high"] > last["bb_up"] and last["close"] < last["bb_up"]:
        sell += 20; why.append("볼린저 상한선 돌파 후 밀림 (단기 저항)")
    elif last["close"] > last["bb_up"]:
        sell += 10; why.append("볼린저 상한선 밖 급등 (과열)")

    # ---- 7) OBV 다이버전스 -------------------------------------------------------------
    div = ind.obv_divergence(df)
    if div["bullish"]:
        buy += 15; why.append("OBV 다이버전스: 가격 횡보/약세인데 OBV 우상향 → 매집 의심")
    if div["bearish"]:
        sell += 10; why.append("OBV 약세 다이버전스: 가격은 오르는데 OBV 하락 → 분산 의심")

    # ---- 8) 거래량 (최근 7일) ----------------------------------------------------------
    vol = ind.volume_analysis(df, 7)
    if vol["ratio"] and vol["up_down_ratio"] is not None:
        if vol["up_down_ratio"] >= 1.5 and vol["ratio"] >= 1.0:
            buy += 5; why.append(f"최근 7일 상승일 거래량이 하락일의 {vol['up_down_ratio']:.1f}배")
        elif vol["up_down_ratio"] <= 0.67:
            sell += 5; warn.append("최근 7일 하락일 거래량 우세 (매도 압력)")

    # ---- 9) 수급: 최근 7일 개인/기관/외국인 (데이터 있을 때만) ----------------------------
    flow_sum = None
    if flows is not None and len(flows):
        f = flows.tail(t["flow_days"])
        flow_sum = {c: int(f[c].sum()) for c in ("individual", "institution", "foreign") if c in f}
        smart = flow_sum.get("institution", 0) + flow_sum.get("foreign", 0)
        if flow_sum.get("institution", 0) > 0 and flow_sum.get("foreign", 0) > 0:
            buy += 15; why.append("최근 7일 기관·외국인 동반 순매수")
        elif smart > 0:
            buy += 7; why.append("최근 7일 기관+외국인 합산 순매수")
        elif smart < 0 and flow_sum.get("individual", 0) > 0:
            sell += 15; warn.append("기관·외국인 순매도를 개인이 받는 구조 (수급 약세)")
    else:
        warn.append("수급(개인/기관/외국인) 데이터 없음 → 해당 항목 미반영")

    # ---- 10) VIX 보정 -------------------------------------------------------------------
    vix_msg, adj = vix_regime(vix, t)
    buy = max(0, buy + adj)
    why.append(vix_msg)

    # ---- 판정 ----------------------------------------------------------------------------
    if sell >= t["sell_threshold"] and sell > buy:
        verdict = "매도/익절 준비"
    elif not ma20_up or not (above("ma60") and above("ma120")):
        # 추세 조건 불충족이면 점수가 높아도 매수로 올리지 않는다 (역추세 진입 방지)
        verdict = "관망 (추세 조건 불충족)" if buy < t["buy_threshold"] else "분할 매수 (역추세, 소량)"
    elif buy >= t["buy_threshold"]:
        verdict = "매수 적기" if (vix is None or vix < t["vix_fear"]) else "분할 매수 (VIX 공포 구간)"
    elif buy >= t["split_buy_threshold"]:
        verdict = "분할 매수 관심"
    else:
        verdict = "관망"

    return Result(code, name, price, int(buy), int(sell), verdict, why, warn, {
        "indicators": {k_: (None if pd.isna(last[k_]) else float(last[k_]))
                       for k_ in ("ma5", "ma20", "ma60", "ma120", "rsi", "stoch_k", "stoch_d",
                                  "macd", "macd_signal", "macd_hist", "bb_up", "bb_mid", "bb_low", "obv")},
        "volume7d": vol, "flow7d": flow_sum, "obv_divergence": div, "vix": vix,
    })
