"""연구용 규칙 변형. BaselineRules(현행 전략의 가격 기반 부분)를 확장한다.
주의: 규칙을 같은 기간을 보고 고른 비교라 독립 검증이 아니다."""
import numpy as np

from .baseline import BaselineRules

EXIT_MODES = ("baseline", "ma20_only", "trail_only", "sell_score_only", "time5", "time10", "none")


class Variant(BaselineRules):
    """기준선(현행) / RSI제외 / B1(20일선 아래 진입 금지) / B2(진입 후 20일선 위 마감을 본 뒤에만 20일선 이탈 청산) + 대체 청산(exit_mode)."""

    def __init__(self, *a, rsi_free=False, b1=False, b2=False, exit_mode="baseline"):
        super().__init__(*a)
        assert exit_mode in EXIT_MODES
        self.rsi_free, self.b1, self.b2, self.exit_mode = rsi_free, b1, b2, exit_mode

    def entry(self, i):
        r, t = self.R[i], self.tr
        ok = (r.verdict == "매수 적기" and r.buy_score >= t.get("buy_min_score", 65) - 15 and self.slope[i] > 0
              and (self.c[i] - self.ma20[i]) / self.atr[i] <= 1.5)
        if not self.rsi_free:
            ok = ok and self.rsi[i] < t.get("buy_max_rsi", 65)
        if self.b1:
            ok = ok and self.c[i] >= self.ma20[i]
        return bool(ok)

    def score(self, i):
        return self.R[i].buy_score

    def exit_signal(self, i, pos):
        t, m = self.tr, self.exit_mode
        if m in ("time5", "time10"):
            return (i + 1 - pos.i0) >= int(m[4:])       # 체결 봉 포함 N 봉 보유 후 다음 시가 청산
        if m == "none":
            return False                                # 손절·목표가로만 청산
        below = self.c[i] < self.ma20[i]
        if self.b2:
            below = below and bool(np.any(self.c[pos.i0:i + 1] >= self.ma20[pos.i0:i + 1]))
        trail = self.c[i] <= self.hi20[i] - t.get("trail_atr", 2.5) * self.atr[i]
        sell = self.R[i].sell_score >= t.get("sell_min_score", 25)
        if m == "ma20_only":
            return bool(below)
        if m == "trail_only":
            return bool(trail)
        if m == "sell_score_only":
            return bool(sell)
        return bool(sell or below or trail or (False if self.rsi_free else self.rsi[i] >= 70))


class Trend20:
    """20일선 추세 벤치마크: 종가가 20일선 위이고 20일선이 상승 중이면 진입, 종가가 20일선 아래로 마감하면 청산. 점수·수급·RSI 없음."""

    def __init__(self, base: BaselineRules):
        self.c, self.ma20, self.slope, self.atr = base.c, base.ma20, base.slope, base.atr

    def entry(self, i):
        return bool(self.c[i] > self.ma20[i] and self.slope[i] > 0)

    def exit_signal(self, i, pos):
        return bool(self.c[i] < self.ma20[i])

    def score(self, i):
        return 0.0


class RandomEntry:
    """순열검정용: 진입일을 days 로 고정하고 청산은 원래 규칙(base)을 그대로 쓴다."""

    def __init__(self, base, days):
        self.base, self.days = base, set(int(d) for d in days)

    def entry(self, i):
        return i in self.days

    def exit_signal(self, i, pos):
        return self.base.exit_signal(i, pos)

    def score(self, i):
        return 0.0
