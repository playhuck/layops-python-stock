"""1a: 백테스트 측정 정정 테스트. 손으로 만든 가격에서 엔진·비용·지표의 약속(시작자산 포함 MDD, 진입일 봉 검사, 손절 우선, 결제일 세율 등)을 고정한다."""
import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockwatch.research import metrics
from stockwatch.research.costs import CostModel, CostScheduleError, load_schedule, settlement_date
from stockwatch.research.engine import Params, SimResult, Trade, buy_and_hold, simulate

COST = CostModel()
BF = COST.buy_factor()
FILL = COST.fill_buy(100.0)          # 시가 100 매수 체결가(슬리피지만). 손절·목표가의 기준 — 수수료는 수량 계산에만 들어간다


def frame(o, h, l, c, start="2025-03-03"):
    idx = pd.bdate_range(start, periods=len(c))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1e6}, index=idx)


def flat(n, px=100.0, start="2025-03-03"):
    return frame([px] * n, [px] * n, [px] * n, [px] * n, start)


class Script:
    def __init__(self, entries=(), exits=()):
        self.e, self.x = set(entries), set(exits)

    def entry(self, i): return i in self.e
    def exit_signal(self, i, pos): return i in self.x


def run(df, rules, market="KSQ", atr=2.0, **kw):
    p = Params(start=0, stop_k=kw.pop("stop_k", 2.0), target_k=kw.pop("target_k", 3.5), **kw)
    return simulate(df, np.full(len(df), float(atr)), rules, p, market, COST)


# ---- 비용 ---------------------------------------------------------------------------------------------
def test_tax_schedule_by_year_and_market():
    assert [COST.tax_rate(date(y, 6, 1), "STK") for y in (2021, 2022, 2023, 2024, 2025, 2026)] == [0.0023, 0.0023, 0.0020, 0.0018, 0.0015, 0.0020]
    assert [COST.tax_rate(date(y, 6, 1), "KSQ") for y in (2021, 2023, 2024, 2025, 2026)] == [0.0023, 0.0020, 0.0018, 0.0015, 0.0020]
    assert load_schedule()[0][0] == date(2021, 1, 1)


def test_costs_before_covered_period_stop_instead_of_silently_using_today_rate():
    with pytest.raises(CostScheduleError):
        COST.tax_rate(date(2020, 12, 31), "STK")
    with pytest.raises(CostScheduleError):
        COST.tax_rate(date(2025, 1, 2), "NASDAQ")


def test_round_trip_cost_2026_is_about_043_percent():
    assert COST.round_trip(date(2026, 6, 1), "STK") == pytest.approx(0.00431, abs=2e-5)
    assert COST.round_trip(date(2025, 6, 1), "KSQ") == pytest.approx(0.00381, abs=2e-5)


def test_tax_follows_settlement_date_not_trade_date_across_year_end():
    # 2025-12-31 휴장. 12-26 체결 → T+2 = 12-30(2025 세율), 12-29 체결 → 2026-01-02(2026 세율)
    idx = pd.DatetimeIndex(["2025-12-24", "2025-12-26", "2025-12-29", "2025-12-30", "2026-01-02", "2026-01-05", "2026-01-06"])
    assert settlement_date(idx, 1) == date(2025, 12, 30) and settlement_date(idx, 2) == date(2026, 1, 2)
    assert COST.tax_rate(settlement_date(idx, 1), "KSQ") == 0.0015 and COST.tax_rate(settlement_date(idx, 2), "KSQ") == 0.0020
    assert settlement_date(idx, 6) == date(2026, 1, 8)                                   # 달력 끝을 넘으면 평일 기준 근사


# ---- 엔진 ---------------------------------------------------------------------------------------------
def test_mdd_includes_starting_equity_so_a_single_losing_trade_is_not_zero_drawdown():
    c = [100, 100, 90, 90, 90, 90]
    df = frame([100, 100, 100, 90, 90, 90], [100] * 3 + [90] * 3, [100, 100, 90, 90, 90, 90], c)
    res = run(df, Script(entries={1}, exits={3}), stop_k=50, target_k=50)                 # 신호 청산만 쓰도록 손절/익절을 멀리
    t = res.trades[0]
    assert t.pnl_pct < -0.10 and res.equity[0] == 1.0
    m = metrics.single(res)
    assert m["mdd"] == pytest.approx(t.pnl_pct, abs=0.01) and m["mdd"] < -0.09            # 이전 코드는 이 경우 0.0% 로 나왔다


def test_entry_bar_low_hitting_stop_closes_the_same_day():
    # 진입 봉(i=2) 안에서 저가가 손절선 아래: 이전 백테스트는 진입일 봉을 검사하지 않아 이 손실을 놓쳤다
    df = frame([100, 100, 100, 100, 100], [100, 100, 101, 100, 100], [100, 100, 90, 100, 100], [100, 100, 95, 100, 100])
    t = run(df, Script(entries={1})).trades[0]
    e = FILL
    assert t.i0 == t.i1 == 2 and t.reason == "손절" and t.exit_px == pytest.approx(e - 2 * 2.0)


def test_stop_wins_when_stop_and_target_touch_in_the_same_bar():
    df = frame([100] * 5, [100, 100, 110, 100, 100], [100, 100, 90, 100, 100], [100] * 5)
    t = run(df, Script(entries={1})).trades[0]
    assert t.reason.startswith("손절") and t.exit_px < 100


def test_gap_below_stop_fills_at_open_by_default_and_waits_in_limit_mode():
    o = [100, 100, 100, 80, 82, 90, 96, 97, 97, 97, 97, 97, 97, 97, 97, 97]
    h = [100, 100, 100, 83, 85, 92, 98, 97, 97, 97, 97, 97, 97, 97, 97, 97]
    l = [100, 100, 100, 79, 80, 88, 94, 96, 96, 96, 96, 96, 96, 96, 96, 96]
    c = [100, 100, 100, 82, 84, 91, 97, 97, 97, 97, 97, 97, 97, 97, 97, 97]
    df = frame(o, h, l, c)
    a = run(df, Script(entries={1})).trades[0]
    assert a.reason == "손절(갭)" and a.exit_px == 80 and a.i1 == 3                          # 시가 체결
    lim = (FILL - 4.0) * 0.985                                                           # 손절선 × (1 - 1.5%) = 지정가
    b = run(df, Script(entries={1}), gap="limit").trades[0]
    assert b.i1 == 6 and b.exit_px == 96 and b.reason == "손절(대기 후 시가)"                    # 6번째 봉 시가(96)가 지정가보다 유리 → 시가 체결
    o2 = list(o); o2[6] = 93.0; l2 = list(l); l2[6] = 92.0                                    # 시가는 지정가 아래, 장중 고가(98)가 지정가를 넘는 경우
    b2 = run(frame(o2, h, l2, c), Script(entries={1}), gap="limit").trades[0]
    assert b2.i1 == 6 and b2.exit_px == pytest.approx(lim) and b2.reason == "손절(대기 후 체결)"   # 지정가 가격으로 체결(실전 OCO 와 같은 방식)


def test_unfilled_limit_stop_is_force_closed_after_pending_days():
    n = 20
    o = [100, 100, 100] + [60] * (n - 3)
    df = frame(o, [100, 100, 100] + [61] * (n - 3), [100, 100, 100] + [59] * (n - 3), [100, 100, 100] + [60] * (n - 3))
    t = run(df, Script(entries={1}), gap="limit").trades[0]
    assert "미체결" in t.reason and t.exit_px == 60


def test_open_position_at_end_is_not_a_realized_trade_but_is_in_the_equity_curve():
    df = flat(8); df.loc[df.index[6:], ["open", "high", "low", "close"]] = 110.0
    res = run(df, Script(entries={4}), stop_k=50, target_k=50)
    assert res.trades == [] and res.open_trade is not None and res.open_trade.reason.startswith("미청산")
    assert res.equity[-1] > 1.05 and res.invested[-1] == 1.0                                    # 평가액은 곡선에, 거래 통계에는 없음
    assert metrics.single(res)["trades"] == 0


def test_flat_price_round_trip_loses_exactly_the_cost_model_amount():
    df = flat(10)
    res = run(df, Script(entries={1}, exits={4}), market="STK", stop_k=50, target_k=50)
    t = res.trades[0]
    settle = settlement_date(df.index, t.i1)
    assert t.pnl_pct == pytest.approx(COST.sell_factor(settle, "STK") / BF - 1, abs=1e-12) and t.pnl_pct < -0.003


def test_exposure_counts_days_with_a_position():
    res = run(flat(10), Script(entries={1}, exits={5}), stop_k=50, target_k=50)
    assert res.invested[:2].sum() == 0 and res.invested[2:6].min() == 1.0 and res.invested[6:].sum() == 0
    assert metrics.single(res)["exposure"] == pytest.approx(np.mean(res.invested[:-0 or None]), abs=1e-12)


def test_buy_and_hold_uses_the_same_valuation_basis():
    df = frame([100, 100, 120], [100, 100, 120], [100, 100, 120], [100, 100, 120])
    bh = buy_and_hold(df, 0, COST)
    assert bh.equity[0] == pytest.approx(1 / BF) and bh.equity[-1] == pytest.approx(1.2 / BF)


# ---- 지표 ---------------------------------------------------------------------------------------------
def tr(pct, amt=None, i0=0, i1=1):
    return Trade(i0, i1, 100, 100, pct if amt is None else amt, pct, "신호")


def test_metrics_known_values():
    eq = np.array([1.0, 1.1, 0.99, 1.089])
    trades = [tr(0.10, i0=0, i1=1), tr(-0.10, i0=1, i1=2), tr(-0.05, i0=2, i1=4), tr(0.20, i0=4, i1=7), tr(-0.01, i0=7, i1=8)]
    m = metrics.compute(eq, np.array([1, 1, 0, 0.5]), trades)
    assert m["total"] == pytest.approx(0.089) and m["mdd"] == pytest.approx(0.99 / 1.1 - 1)
    assert m["trades"] == 5 and m["win"] == pytest.approx(0.4) and m["pf"] == pytest.approx(0.30 / 0.16)
    assert m["max_loss_streak"] == 2 and m["hold_mean"] == pytest.approx(1.6) and m["hold_median"] == 1.0
    assert m["exposure"] == pytest.approx(0.625) and m["avg"] == pytest.approx(0.028) and m["median"] == pytest.approx(-0.01)
    r = np.diff(eq) / eq[:-1]
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))
    assert m["sortino"] == pytest.approx(r.mean() / math.sqrt(np.mean(np.minimum(r, 0) ** 2)) * math.sqrt(252))


def test_profit_factor_without_losses_is_infinite_and_empty_trades_are_zero():
    assert metrics.compute(np.array([1.0, 1.1]), np.array([1, 1]), [tr(0.1)])["pf"] == float("inf")
    z = metrics.compute(np.array([1.0, 1.0, 1.0]), np.zeros(3), [])
    assert z["trades"] == 0 and z["pf"] == 0.0 and z["sharpe"] == 0.0 and z["exposure"] == 0.0


def test_window_is_simulated_from_cash_and_never_sees_later_prices():
    from stockwatch.research.engine import simulate_window
    c = [100, 100, 100, 100, 100, 100, 130, 130, 130, 130, 130, 130]
    df = frame(c, c, c, c); rules = Script(entries={1}, exits={9}); atr = np.full(12, 2.0); p = Params(start=0, stop_k=50, target_k=50)
    full = simulate(df, atr, rules, p, "KSQ", COST)
    first = simulate_window(df, atr, rules, p, "KSQ", COST, 0, 6)
    second = simulate_window(df, atr, rules, p, "KSQ", COST, 6, 12)
    # 후반부(9일)에 청산되는 거래의 +30% 가 전반부에 섞이지 않는다: 전반부는 미청산 포지션(평가손익 ≈ 0)일 뿐이다
    assert len(full.trades) == 1 and first.trades == [] and first.open_trade is not None and metrics.single(first)["trades"] == 0
    assert metrics.single(first)["total"] == pytest.approx(first.equity[5] - 1) and abs(metrics.single(first)["total"]) < 0.01
    assert second.equity[6] == 1.0 and second.trades == [] and metrics.single(second)["exposure"] == 0.0           # 경계에서 포지션을 이월하지 않고 현금에서 시작
    df2 = df.copy(); df2.iloc[6:, df2.columns.get_indexer(["open", "high", "low", "close"])] = 10.0                 # 6일 이후 가격을 바꿔도 전반부 결과는 같다
    assert np.array_equal(simulate_window(df2, atr, rules, p, "KSQ", COST, 0, 6).equity[:6], first.equity[:6])


def test_signal_exit_at_next_open_takes_priority_over_that_bars_intraday_stop():
    # 결정일 종가에 매도 신호 → 다음 날 시가(100) 청산. 같은 날 장중 저가(80)가 손절선에 닿아도 시가 청산이 시간상 먼저다
    df = frame([100] * 5, [100, 100, 100, 101, 100], [100, 100, 100, 80, 100], [100] * 5)
    t = run(df, Script(entries={1}, exits={2})).trades[0]
    assert t.reason == "신호" and t.exit_px == 100 and t.i1 == 3
    # 이전의 보수적 순서(장중 손절 먼저)도 옵션으로 남아 있다
    t2 = run(df, Script(entries={1}, exits={2}), exit_priority="stop").trades[0]
    assert t2.reason == "손절" and t2.exit_px == pytest.approx(FILL - 4.0)
    # 신호가 없으면 손절은 그대로 작동한다
    assert run(df, Script(entries={1})).trades[0].reason == "손절"


def test_sleeve_portfolio_is_equal_weight_without_rebalancing():
    d = pd.bdate_range("2025-03-03", periods=3)
    a = SimResult(3, 0, d, np.array([1.0, 2.0, 4.0]), np.array([1.0, 1, 1]), [], None)
    b = SimResult(3, 0, d, np.array([1.0, 1.0, 1.0]), np.zeros(3), [], None)
    m = metrics.sleeve_portfolio([a, b])
    assert m["total"] == pytest.approx(1.5) and m["exposure"] == pytest.approx(0.5)          # (4+1)/2 - 1, 평균 투자비중 50%


# ---- 현행 규칙 연결(스모크) ---------------------------------------------------------------------------
def test_baseline_rules_run_end_to_end_on_synthetic_data_without_lookahead():
    from stockwatch import indicators as ind
    from stockwatch.research.baseline import BaselineRules, precompute_signals
    t = np.arange(220)
    close = 100_000 * (1 + 0.002 * t) + 2_500 * np.sin(t / 3)
    df = frame(close * 0.998, close * 1.01, close * 0.99, close)
    d = ind.add_all(df)
    R = precompute_signals(df, None, 130)
    assert set(R) == set(range(130, len(df) - 1))
    tr_cfg = {"buy_min_score": 65, "buy_max_rsi": 101, "trail_atr": 2.5, "sell_min_score": 25}
    res = simulate(df, d["atr"].to_numpy(), BaselineRules(df, d, R, tr_cfg), Params(), "STK", COST)
    assert len(res.equity) == len(df) and res.equity[130] == 1.0 and np.isfinite(res.equity[130:]).all()
    # 미래 정보 없음: 마지막 며칠 가격을 크게 바꿔도 그 이전 날짜의 신호/자산은 변하지 않는다
    df2 = df.copy(); df2.iloc[-3:, df2.columns.get_indexer(["open", "high", "low", "close"])] *= 0.5
    d2 = ind.add_all(df2)
    res2 = simulate(df2, d2["atr"].to_numpy(), BaselineRules(df2, d2, precompute_signals(df2, None, 130), tr_cfg), Params(), "STK", COST)
    assert np.allclose(res.equity[130:-5], res2.equity[130:-5])


def test_stop_and_target_use_the_fill_price_not_the_commission_inclusive_cost():
    # 진입가 e 에 수수료가 섞이면 손절·목표가가 실전(체결가 기준 보호주문)과 어긋난다
    df = frame([100] * 6, [100, 100, 100, 100, 100, 100], [100, 100, 100, 100, 100, 100], [100] * 6)
    r = run(df, Script(entries={1}))
    assert r.open_trade is not None and r.open_trade.entry_px == pytest.approx(COST.fill_buy(100.0))
    assert COST.fill_buy(100.0) == pytest.approx(100 * (1 + COST.slippage_buy))                 # 수수료 미포함


def test_buy_and_hold_cost_is_not_normalized_away():
    df = flat(10); bh = buy_and_hold(df, 3, COST)
    from stockwatch.research import metrics
    m = metrics.single(bh)
    assert m["total"] == pytest.approx(1 / COST.buy_factor() - 1) and m["total"] < 0         # 평평한 가격이면 매수 비용만큼 손실


def test_incomplete_todays_bar_is_dropped_before_close_only():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from stockwatch.research.data import complete_bars
    df = flat(5); last = df.index[-1].to_pydatetime()
    kst = ZoneInfo("Asia/Seoul")
    assert len(complete_bars(df, datetime(last.year, last.month, last.day, 15, 22, tzinfo=kst))) == 4   # 장 마감 전: 오늘 봉 제외
    assert len(complete_bars(df, datetime(last.year, last.month, last.day, 15, 45, tzinfo=kst))) == 5   # 마감 후: 유지
    nxt = df.index[-1] + pd.Timedelta(days=1)
    assert len(complete_bars(df, datetime(nxt.year, nxt.month, nxt.day, 9, 5, tzinfo=kst))) == 5          # 다음 날 장중: 어제 봉은 완성
