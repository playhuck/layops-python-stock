"""성과 지표. 모든 지표는 일별 평가 자산곡선(시작=1.0)과 실현된 거래만으로 계산한다."""
import math

import numpy as np

from .engine import SimResult

PPY = 252  # 연환산 거래일 수


def _slice(res: SimResult):
    """결과 전체를 시작=1.0 으로 정규화. (구간 통계는 자르지 않고 구간마다 simulate_window 로 새로 돌린다: 자르면 구간 밖 거래 결과가 섞인다)"""
    eq = res.equity[res.start:res.n]
    return eq / eq[0], res.invested[res.start:res.n], list(res.trades)


def compute(eq: np.ndarray, inv: np.ndarray, trades: list) -> dict:
    """eq[0] == 1.0 (시작 자산 포함). 반환 단위: 비율(0.1 = 10%)."""
    m = {"days": len(eq), "trades": len(trades)}
    years = (len(eq) - 1) / PPY
    m["total"] = float(eq[-1] - 1)
    m["cagr"] = float(eq[-1] ** (1 / years) - 1) if years > 0 and eq[-1] > 0 else float("nan")
    m["mdd"] = float((eq / np.maximum.accumulate(eq) - 1).min())                      # 시작 자산(1.0)이 포함된 곡선 → 첫 거래 손실도 낙폭
    r = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([])
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    m["sharpe"] = float(r.mean() / sd * math.sqrt(PPY)) if sd > 0 else 0.0              # 무위험수익률 0 가정, 현금 구간 포함
    dd = math.sqrt(float(np.mean(np.minimum(r, 0.0) ** 2))) if len(r) else 0.0           # 하방편차(목표 0, 전체 관측 기준)
    m["sortino"] = float(r.mean() / dd * math.sqrt(PPY)) if dd > 0 else 0.0
    m["exposure"] = float(np.mean(inv)) if len(inv) else 0.0                              # 자본 노출률: 평균 투자 비중(현금 포함 자본 대비)
    if trades:
        ts = sorted(trades, key=lambda t: t.i1)
        pct, amt = np.array([t.pnl_pct for t in ts]), np.array([t.pnl_amt for t in ts])
        gain, loss = amt[amt > 0].sum(), -amt[amt < 0].sum()
        hold = np.array([t.i1 - t.i0 for t in ts])
        streak = best = 0
        for x in pct:
            streak = streak + 1 if x < 0 else 0
            best = max(best, streak)
        m.update(win=float((pct > 0).mean()), avg=float(pct.mean()), median=float(np.median(pct)), worst=float(pct.min()), best=float(pct.max()),
                 pf=float(gain / loss) if loss > 0 else (float("inf") if gain > 0 else 0.0),
                 hold_mean=float(hold.mean()), hold_median=float(np.median(hold)), max_loss_streak=int(best))
    else:
        m.update(win=0.0, avg=0.0, median=0.0, worst=0.0, best=0.0, pf=0.0, hold_mean=0.0, hold_median=0.0, max_loss_streak=0)
    return m


def single(res: SimResult) -> dict:
    eq, inv, trades = _slice(res)
    return compute(eq, inv, trades)


def sleeve_portfolio(results: list[SimResult]) -> dict:
    """등가중 sleeve 포트폴리오: 자본을 종목 수로 균등 배분(리밸런싱 없음), 각 sleeve 는 해당 종목에만 100% 투입, 미투자 현금 수익 0.
    자본을 종목끼리 공유하지 않는다. (실전식 자본 공유 포트폴리오는 별도 모드: 종목당 비중·최대 보유 수·업종 한도 필요)"""
    parts = [_slice(r) for r in results]
    eq, inv = sleeve_equity(results)
    trades = [t for p in parts for t in p[2]]
    return compute(eq, inv, trades)


def sleeve_equity(results: list[SimResult]) -> tuple[np.ndarray, np.ndarray]:
    """등가중 sleeve 포트폴리오의 (자산곡선, 투자 비중). 시작=1.0. 부트스트랩 등이 일별 수익률을 만들 때 쓴다."""
    parts = [_slice(r) for r in results]
    return np.mean([p[0] for p in parts], axis=0), np.mean([p[1] for p in parts], axis=0)
