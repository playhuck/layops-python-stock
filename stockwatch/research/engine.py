"""단일 종목 백테스트 엔진 (sleeve: 그 종목에 자본 100% 투입/전량 청산).

측정상의 약속 (이전 backtest.py 의 오류를 바로잡은 부분)
- 자산곡선은 '시작 자산 1.0'에서 출발하는 일별 종가 평가 곡선이다(보유 중 평가손익 포함). → MDD 가 첫 거래 손실을 놓치지 않는다.
- 진입일(체결 봉)의 고가·저가도 손절/목표가 검사에 쓴다.
- 같은 봉에서 손절가와 목표가가 모두 닿으면 일봉으로는 순서를 알 수 없으므로 손절 우선(보수적).
- 매수 체결가 = 시가 × (1+수수료+슬리피지), 매도 체결가 = 가격 × (1−수수료−슬리피지−세금). 세금은 양도일(결제일 T+2)의 시장별 세율.
- 마지막 날 보유 중인 포지션은 '실현된 거래'가 아니다: 승률·PF 등 거래 통계에서 빼고, 평가액만 자산곡선에 반영한다.
- 신호는 결정일 i 의 종가까지의 정보로 만들고, 체결은 다음 봉(i+1) 시가다. 따라서 전날 매도 신호는 그 봉의 장중 손절·익절보다 시간상 먼저 처리한다(exit_priority="signal", 기본).
- 구간(전반부/후반부) 통계는 구간마다 현금에서 새로 시뮬레이션한다(simulate_window). 경계에서 포지션을 이월하지 않으므로 거래와 자산곡선이 같은 구간 안의 정보만 쓴다.
"""
from dataclasses import dataclass, field, replace
from typing import Protocol

import numpy as np
import pandas as pd

from .costs import CostModel, settlement_date


@dataclass(frozen=True)
class Params:
    stop_k: float = 2.0          # 손절 = 진입가 − k×ATR
    target_k: float = 3.5        # 익절 = 진입가 + k×ATR
    start: int = 130             # 지표 워밍업 이후 첫 평가 인덱스
    gap: str = "open"            # "open": 손절선 아래로 갭하면 시가 체결 / "limit": 실전 OCO 처럼 손절선×(1-buffer) 지정가(안 닿으면 대기)
    limit_buffer: float = 0.015
    pending_max_days: int = 10   # 지정가가 안 닿고 이 일수가 지나면 그날 종가로 강제 정리(보수적 근사)
    exit_priority: str = "signal"  # "signal": 전날 신호 청산(시가)이 먼저 / "stop": 장중 손절·익절을 먼저(이전의 보수적 순서)


@dataclass
class Position:
    i0: int
    entry: float
    stop: float
    target: float
    shares: float
    eq0: float
    pend: float | None = None
    wait: int = 0
    state: dict = field(default_factory=dict)    # 규칙이 쓰는 임의 상태


@dataclass
class Trade:
    i0: int
    i1: int
    entry_px: float
    exit_px: float
    pnl_amt: float
    pnl_pct: float
    reason: str
    entry_date: object = None
    exit_date: object = None


@dataclass
class SimResult:
    n: int
    start: int
    dates: pd.DatetimeIndex
    equity: np.ndarray
    invested: np.ndarray
    trades: list
    open_trade: Trade | None


class Rules(Protocol):
    def entry(self, i: int) -> bool: ...                        # 결정일 i 종가 기준 진입 신호 → i+1 시가 체결
    def exit_signal(self, i: int, pos: Position) -> bool: ...   # 결정일 i 종가 기준 청산 신호 → i+1 시가 체결


def simulate(df: pd.DataFrame, atr: np.ndarray, rules: Rules, params: Params, market: str, cost: CostModel) -> SimResult:
    """모든 진입 신호를 허용하는 단일 종목 시뮬레이션(sleeve)."""
    g = simulate_steps(df, atr, rules, params, market, cost)
    try:
        next(g)
        while True:
            g.send(True)
    except StopIteration as stop:
        return stop.value


def simulate_steps(df: pd.DataFrame, atr: np.ndarray, rules: Rules, params: Params, market: str, cost: CostModel, trades: list | None = None):
    """simulate 의 제너레이터 형태. 진입 신호가 난 결정일 i 마다 i 를 yield 하고, 호출자가 send(True/False) 로 진입 허용 여부를 돌려준다.
    (여러 종목이 자본을 공유하는 포트폴리오 시뮬레이션이 날짜순으로 진입을 조율하는 데 쓴다.) 이 시점에 포지션은 항상 없다. 종료 시 SimResult 를 반환한다.
    trades 를 넘기면 그 목록에 닫힌 거래가 바로 쌓인다(호출자가 중간 상태를 볼 수 있다)."""
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    n, dates = len(df), df.index
    eq, inv = np.full(n, np.nan), np.zeros(n)
    st = {"cash": 1.0, "pos": None}
    trades = [] if trades is None else trades

    def close_pos(b: int, px: float, reason: str):
        p = st["pos"]
        proceeds = p.shares * px * cost.sell_factor(settlement_date(dates, b), market)
        st["cash"] += proceeds
        trades.append(Trade(p.i0, b, p.entry, px, proceeds - p.eq0, proceeds / p.eq0 - 1, reason, dates[p.i0].date(), dates[b].date()))
        st["pos"] = None

    def intrabar(b: int) -> bool:
        """봉 b 에서의 손절/목표가. 포지션이 닫히면 True."""
        p = st["pos"]
        if p.pend is not None:                                   # 갭으로 지정가 손절이 안 걸린 채 대기 중
            p.wait += 1
            if o[b] >= p.pend: close_pos(b, o[b], "손절(대기 후 시가)"); return True
            if h[b] >= p.pend: close_pos(b, p.pend, "손절(대기 후 체결)"); return True
            if p.wait >= params.pending_max_days: close_pos(b, c[b], "손절(미체결→종가 정리)"); return True
            return False
        if o[b] <= p.stop or l[b] <= p.stop:                     # 손절 우선: 같은 봉에 목표가가 닿았어도 손절로 처리
            gap_down = o[b] <= p.stop
            if params.gap == "open" or not gap_down:
                close_pos(b, o[b] if gap_down else p.stop, "손절(갭)" if gap_down else "손절"); return True
            lim = p.stop * (1 - params.limit_buffer)
            if o[b] >= lim: close_pos(b, o[b], "손절(갭, 지정가 안)"); return True
            if h[b] >= lim: close_pos(b, lim, "손절(갭, 장중 체결)"); return True
            p.pend, p.wait = lim, 0
            return False
        if h[b] >= p.target:
            close_pos(b, max(p.target, o[b]), "익절"); return True
        return False

    for i in range(params.start, n - 1):
        p, b = st["pos"], i + 1
        eq[i] = st["cash"] + (p.shares * c[i] if p else 0.0)
        inv[i] = (p.shares * c[i] / eq[i]) if p else 0.0
        if p is not None:
            # 결정일 i 종가의 매도 신호는 다음 봉 시가에 체결된다 → 그 봉 장중의 손절·익절보다 시간상 먼저.
            # "stop" 은 이전의 보수적 순서(장중 손절·익절 먼저). 실전은 승인이 장중에 일어나고 그동안 서버 손절 OCO 가 살아 있어 두 가정의 중간에 가깝다.
            if params.exit_priority == "signal" and p.pend is None and rules.exit_signal(i, p):
                close_pos(b, o[b], "신호")
                continue
            if intrabar(b) or st["pos"] is None or st["pos"].pend is not None:
                continue
            if params.exit_priority == "stop" and rules.exit_signal(i, st["pos"]):
                close_pos(b, o[b], "신호")
            continue
        if np.isnan(atr[i]) or not rules.entry(i):
            continue
        if not (yield i):                                        # 호출자가 진입을 허용하지 않음
            continue
        e = cost.fill_buy(o[b])                                  # 체결가(슬리피지 포함). 손절·목표가 기준
        st["pos"] = Position(i0=b, entry=e, stop=e - params.stop_k * atr[i], target=e + params.target_k * atr[i],
                             shares=st["cash"] / (e * (1 + cost.commission)), eq0=st["cash"])   # 수수료는 살 수 있는 수량에서 차감
        st["cash"] = 0.0
        intrabar(b)                                              # 진입일 봉의 손절/목표가도 검사

    p = st["pos"]
    last = n - 1
    eq[last] = st["cash"] + (p.shares * c[last] if p else 0.0)
    inv[last] = (p.shares * c[last] / eq[last]) if p else 0.0
    open_trade = None
    if p is not None:   # 미청산: 거래 통계에는 넣지 않고 평가손익만 따로 기록 (매도 비용은 아직 안 나갔으므로 반영하지 않음)
        mtm = p.shares * c[last]
        open_trade = Trade(p.i0, last, p.entry, c[last], mtm - p.eq0, mtm / p.eq0 - 1, "미청산(종가 평가)", dates[p.i0].date(), dates[last].date())
    return SimResult(n, params.start, dates, eq, inv, trades, open_trade)


def simulate_window(df: pd.DataFrame, atr: np.ndarray, rules: Rules, params: Params, market: str, cost: CostModel, lo: int, hi: int) -> SimResult:
    """[lo, hi) 구간을 현금 100%에서 새로 시작해 시뮬레이션. hi 이후 데이터는 보지 않고, 경계의 포지션은 이월하지 않는다."""
    return simulate(df.iloc[:hi], atr[:hi], rules, replace(params, start=lo), market, cost)


def buy_and_hold(df: pd.DataFrame, start: int, cost: CostModel) -> SimResult:
    """start 종가에 전액 매수(매수 비용 반영)해 끝까지 보유. 전략과 같은 기준으로 마지막 청산 비용은 반영하지 않는다(평가액)."""
    c = df["close"].to_numpy(dtype=float); n = len(c)
    sh = 1.0 / (c[start] * cost.buy_factor())
    eq = np.full(n, np.nan); eq[start - 1] = 1.0; eq[start:] = sh * c[start:]     # 시작 자산 1.0(현금) 다음 날부터 매수 비용이 반영된 평가액 → 지표가 다시 정규화해도 비용이 상쇄되지 않는다
    inv = np.zeros(n); inv[start:] = 1.0
    return SimResult(n, start - 1, df.index, eq, inv, [], None)
