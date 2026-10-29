"""현행 전략(실전 엔진과 같은 진입·청산 규칙)의 가격 기반 부분. 수급·VIX·KOSPI 국면·업종 한도는 재현하지 않는다(과거 이력이 없거나 1b 범위)."""
import numpy as np
import pandas as pd

from .. import indicators as ind
from ..signals import analyze


def precompute_signals(df: pd.DataFrame, thresholds: dict | None, start: int) -> dict:
    """날짜별 분석 결과. 그 날까지의 데이터만 넣는다(미래 정보 없음). 느리다(O(T²)) — signals 분리 리팩터링 후 개선 예정."""
    return {i: analyze("BT", "BT", df.iloc[: i + 1], None, None, thresholds) for i in range(start, len(df) - 1)}


class BaselineRules:
    def __init__(self, df: pd.DataFrame, d: pd.DataFrame, R: dict, tr: dict):
        self.R, self.tr = R, tr
        self.c, self.ma20, self.atr, self.rsi = (d[k].to_numpy(dtype=float) for k in ("close", "ma20", "atr", "rsi"))
        self.hi20 = d["close"].rolling(20).max().to_numpy(dtype=float)
        self.slope = np.array([ind.slope_pct(d["ma20"].iloc[: i + 1], 5) if i >= 30 else np.nan for i in range(len(d))])

    def entry(self, i: int) -> bool:
        r, t = self.R[i], self.tr
        # 실전은 수급 가점(최대 15)이 점수에 포함되므로, 수급 이력이 없는 백테스트는 임계값에서 15를 뺀다 (근사: 1b 에서 수급 이력으로 대체)
        return bool(r.verdict == "매수 적기" and r.buy_score >= t.get("buy_min_score", 65) - 15 and self.slope[i] > 0
                    and self.rsi[i] < t.get("buy_max_rsi", 65) and (self.c[i] - self.ma20[i]) / self.atr[i] <= 1.5)

    def exit_signal(self, i: int, pos) -> bool:
        t = self.tr
        trail = self.c[i] <= self.hi20[i] - t.get("trail_atr", 2.5) * self.atr[i]
        return bool(self.R[i].sell_score >= t.get("sell_min_score", 25) or self.c[i] < self.ma20[i] or trail or self.rsi[i] >= 70)
