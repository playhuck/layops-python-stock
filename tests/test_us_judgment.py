from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockwatch import us_judgment as uj

TODAY = date(2026, 10, 30)
GOOD_MKT = {"ok": True, "text": "VIX 16.0 · 나스닥 종합 50일선 위"}
KNOWN = {"next_earnings": date(2026, 12, 15)}          # 실적일이 멀리 있다고 알려진 종목


def frame(n=140, start=100.0, step=0.3, end="2026-10-29", vol=3_000_000.0, noise=3.0):
    idx = pd.bdate_range(end=end, periods=n)
    c = start + np.arange(n) * step + noise * np.sin(np.arange(n))
    return pd.DataFrame({"open": c * 0.999, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": vol}, index=idx)


def bench(n=140, step=0.05):
    return frame(n, 1000.0, step)


def review(df=None, **kw):
    kw.setdefault("mkt", GOOD_MKT); kw.setdefault("bench", bench()); kw.setdefault("today", TODAY); kw.setdefault("meta", KNOWN)
    return uj.buy_review(frame() if df is None else df, **kw)


def passed(r, label_part):
    return next(e for e in r["evidence"] if label_part in e["label"])["passed"]


def test_healthy_uptrend_pullback_is_a_buy_review_with_levels():
    r = review()
    assert r["verdict"]["code"] == "review" and r["unmet"] == 0
    lv = r["levels"]
    assert lv["stop"] < lv["ref"] < lv["target"]
    assert lv["rr"] == pytest.approx(uj.TARGET_K / uj.STOP_K)
    assert lv["stop"] == pytest.approx(lv["ref"] - uj.STOP_K * lv["atr"])


def test_evidence_shape_checks_have_passed_and_info_has_none():
    ev = review()["evidence"]
    checks = [e for e in ev if e.get("kind") != "info"]
    infos = [e for e in ev if e.get("kind") == "info"]
    assert checks and infos
    assert all(isinstance(e["passed"], bool) for e in checks)
    assert all("passed" not in e for e in infos)


def test_earnings_within_a_week_blocks_even_when_everything_else_passes():
    r = review(meta={"next_earnings": date(2026, 11, 3)})                 # D-4
    assert r["verdict"]["code"] == "blocked"
    assert passed(r, "실적 발표") is False
    far = review(meta={"next_earnings": date(2026, 11, 20)})
    assert far["verdict"]["code"] == "review" and passed(far, "실적 발표") is True


def test_unknown_or_past_earnings_date_is_a_labeled_review_not_a_plain_one():
    for meta in ({}, {"next_earnings": date(2026, 10, 1)}):
        r = review(meta=meta)
        assert r["verdict"]["code"] == "review_check" and r["verdict"]["label"] == "매수 검토 · 실적일 확인 필요"
        assert "직접 확인" in r["verdict"]["reason"]
        assert any(e.get("kind") == "info" and e["label"] == "실적 발표 일정" for e in r["evidence"])
    assert review(meta=KNOWN)["verdict"]["code"] == "review"                      # 일정이 확인되면 그냥 '매수 검토'
    blocked = review(meta={"next_earnings": date(2026, 11, 3)})
    assert blocked["verdict"]["code"] == "blocked"                                  # 실적 임박은 여전히 차단


def test_low_dollar_volume_and_stale_data_block():
    assert review(frame(vol=1_000.0))["verdict"]["code"] == "blocked"
    old = frame(end="2026-10-14")                                           # 오늘(10/30)보다 16일 전 마지막 봉
    assert review(old)["verdict"]["code"] == "blocked"


def test_downtrend_is_watch_with_unmet_count():
    r = review(frame(step=-0.3, start=170.0))
    assert r["verdict"]["code"] == "watch" and r["unmet"] >= 1
    assert passed(r, "중장기 추세") is False


def test_weaker_than_nasdaq_fails_relative_strength():
    r = review(bench=bench(step=10.0))                                      # 나스닥 20일 수익률이 훨씬 더 큼
    assert passed(r, "나스닥보다 강함") is False and r["verdict"]["code"] == "watch"


def test_bad_market_regime_fails_the_check():
    r = review(mkt={"ok": False, "text": "VIX 31.0 · 나스닥 종합 50일선 아래"})
    assert passed(r, "시장 환경") is False and r["verdict"]["code"] == "watch"


def test_unknown_regime_or_missing_bench_holds_the_verdict_instead_of_guessing():
    r = review(mkt={"ok": None, "text": "VIX 확인 불가"})
    assert r["verdict"]["code"] == "na" and "시장 환경" in r["verdict"]["reason"]
    r2 = uj.buy_review(frame(), mkt=GOOD_MKT, bench=None, today=TODAY)
    assert r2["verdict"]["code"] == "na" and "상대강도" in r2["verdict"]["reason"]


def test_too_few_rows_or_no_data_is_unavailable_without_raising():
    assert uj.buy_review(frame(n=30), today=TODAY)["verdict"]["code"] == "na"
    assert uj.buy_review(None, today=TODAY)["verdict"]["code"] == "na"
    assert uj.sell_review(frame(n=30), {"qty": 1, "avg_price": 100}, today=TODAY)["verdict"]["code"] == "na"


def test_inputs_are_not_mutated():
    df = frame(); before = df.copy()
    review(df)
    uj.sell_review(df, {"qty": 1, "avg_price": 100.0}, mkt=GOOD_MKT, today=TODAY)
    pd.testing.assert_frame_equal(df, before)


# -- 매도 ---------------------------------------------------------------------------------------
def sell(df=None, avg=0.95, qty=10, opened=None, **kw):
    """avg 는 마지막 종가에 대한 비율(1.0 = 본전)."""
    kw.setdefault("mkt", GOOD_MKT); kw.setdefault("today", TODAY)
    df = frame() if df is None else df
    pos = {"qty": qty, "avg_price": avg * float(df["close"].iloc[-1]), "opened_on": opened}
    return uj.sell_review(df, pos, **kw)


def test_holding_in_a_healthy_trend_is_hold_with_pnl():
    r = sell(avg=0.95)
    assert r["verdict"]["code"] == "hold"
    assert r["pnl_pct"] == pytest.approx((1 / 0.95 - 1) * 100)
    assert r["pnl_value"] == pytest.approx(r["price"] * (1 - 0.95) * 10)


def test_stop_line_breach_is_a_sell_review():
    r = sell(avg=1.4)                                                       # 평단이 현재가보다 훨씬 높다
    assert r["verdict"]["code"] == "sell" and passed(r, "손절선") is True


def test_target_reached_is_a_sell_review():
    r = sell(avg=0.5)
    assert r["verdict"]["code"] == "sell" and passed(r, "목표가") is True


def test_break_of_ma20_alone_triggers_sell_review():
    df = frame()
    df.iloc[-1, df.columns.get_loc("close")] = df["close"].iloc[-1] * 0.93
    r = sell(df, avg=1.02)
    assert passed(r, "20일선 이탈") is True and r["verdict"]["code"] == "sell"


def test_trailing_stop_ignores_highs_from_before_the_entry():
    idx = pd.bdate_range(end="2026-10-29", periods=140)
    c = np.full(140, 100.0); c[-30:-10] = 160.0; c[-10:] = 100.0          # 진입 전에 있었던 160 고점
    df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 3e6}, index=idx)
    entry = idx[-8].date()
    with_entry = sell(df, avg=1.0, opened=entry)
    assert passed(with_entry, "트레일링") is False                        # 진입 이후 고점은 100 뿐
    without = sell(df, avg=1.0, opened=None)
    assert passed(without, "트레일링") is True                            # 날짜를 모르면 20일 고점(160)을 쓴다


def test_just_registered_position_with_no_bars_since_entry_uses_the_avg_as_the_high():
    r = sell(avg=0.98, opened=date(2026, 10, 30))                         # 마지막 완성 봉(10/29) 다음 날 진입
    assert any("트레일링" in e["label"] for e in r["evidence"])
    assert r["levels"]["trail"] == pytest.approx(r["price"] - uj.TRAIL_K * r["levels"]["atr"])      # 고점은 max(평단, 현재가)


def test_soft_conditions_give_caution_not_sell():
    r = sell(avg=0.95, meta={"next_earnings": date(2026, 11, 2)})
    assert r["verdict"]["code"] == "caution" and "실적" in r["verdict"]["reason"]
    r2 = sell(avg=0.95, mkt={"ok": False, "text": "VIX 31.0 · 나스닥 종합 50일선 아래"})
    assert r2["verdict"]["code"] == "caution"


def test_regime():
    idx = pd.bdate_range(end="2026-10-29", periods=80)
    up = pd.DataFrame({"close": np.linspace(1000, 1160, 80)}, index=idx)
    vix = lambda v, end="2026-10-29": pd.Series([v], index=pd.to_datetime([end]))
    r = uj.regime(vix(16.0), up, today=TODAY)
    assert r["ok"] is True and "2026-10-29 마감 기준" in r["text"] and r["as_of"] == "2026-10-29"
    assert uj.regime(vix(31.0), up, today=TODAY)["ok"] is False
    down = pd.DataFrame({"close": np.linspace(1160, 1000, 80)}, index=idx)
    assert uj.regime(vix(16.0), down, today=TODAY)["ok"] is False
    assert uj.regime(None, up, today=TODAY)["ok"] is None and uj.regime(vix(16.0), None, today=TODAY)["ok"] is None
    assert uj.regime(vix(31.0), None, today=TODAY)["ok"] is False                 # 하나라도 나쁘면 확인 불가보다 나쁨이 우선


def test_regime_refuses_mismatched_or_stale_dates():
    idx = pd.bdate_range(end="2026-10-29", periods=80)
    up = pd.DataFrame({"close": np.linspace(1000, 1160, 80)}, index=idx)
    old_vix = pd.Series([16.0], index=pd.to_datetime(["2026-09-01"]))
    r = uj.regime(old_vix, up, today=TODAY)
    assert r["ok"] is None and "달라" in r["text"]                                 # 기준일 불일치: 한 달 전 VIX 로 정상 판정이 나면 안 된다
    old_both = pd.DataFrame({"close": np.linspace(1000, 1160, 80)}, index=pd.bdate_range(end="2026-10-01", periods=80))
    r2 = uj.regime(pd.Series([16.0], index=pd.to_datetime(["2026-10-01"])), old_both, today=TODAY)
    assert r2["ok"] is None and "오래" in r2["text"]                                # 둘이 같아도 오래됐으면 확인 불가
    bad_but_stale = uj.regime(pd.Series([40.0], index=pd.to_datetime(["2026-10-01"])), old_both, today=TODAY)
    assert bad_but_stale["ok"] is None                                              # 오래된 값은 나쁜 쪽으로도 쓰지 않는다


def test_regime_with_unmatched_mkt_blocks_the_buy_verdict():
    r = review(mkt={"ok": None, "text": "VIX 기준일(2026-09-01)과 나스닥 기준일(2026-10-29)이 달라 확인할 수 없어요"})
    assert r["verdict"]["code"] == "na" and "시장 환경" in r["verdict"]["reason"]


# -- A단계: 최소 시세·매도 최신성·상대강도 정렬 ------------------------------------------------------
def test_buy_requires_the_120_day_average_so_new_listings_are_held_back():
    r = review(frame(n=80), bench=bench(80))
    assert r["verdict"]["code"] == "na" and "120일선" in r["verdict"]["reason"] and r["evidence"] == []
    assert review(frame(n=124), bench=bench(124))["verdict"]["code"] == "na"
    assert review(frame(n=125), bench=bench(125))["verdict"]["code"] in {"review", "watch"}   # 120일선이 있으면 판단한다


def test_sell_works_for_short_history_but_not_below_the_minimum():
    assert sell(frame(n=45), avg=0.95)["verdict"]["code"] in {"hold", "caution", "sell"}
    assert sell(frame(n=39), avg=0.95)["verdict"]["code"] == "na"


def test_stale_prices_never_produce_hold_but_real_exit_signals_still_show():
    old = frame(end="2026-10-01")                                                   # 마지막 봉이 29일 전
    held = sell(old, avg=0.95)
    assert held["verdict"]["code"] == "na" and "29일 전" in held["verdict"]["reason"]
    stop = sell(old, avg=1.6)                                                       # 마지막 확인 시점에 이미 손절선 아래
    assert stop["verdict"]["code"] == "sell" and "29일 전" in stop["verdict"]["reason"] and "손절선" in stop["verdict"]["reason"]
    soft = sell(old, avg=0.95, meta={"next_earnings": date(2026, 11, 2)})
    assert soft["verdict"]["code"] == "caution" and "29일 전" in soft["verdict"]["reason"]
    fresh = sell(avg=0.95)
    assert fresh["verdict"]["code"] == "hold" and any(e["label"] == "시세 최신성" for e in fresh["evidence"])


def test_pnl_label_states_what_is_excluded():
    head = sell(avg=0.95)["evidence"][0]
    assert head["label"] == "보유" and all(w in head["threshold"] for w in ("수수료", "세금", "환율", "배당"))


def test_relative_strength_uses_common_trading_days():
    idx = pd.bdate_range(end="2026-10-29", periods=40)
    stock = pd.Series(np.linspace(100, 140, 40), index=idx)
    nas = pd.Series(np.linspace(1000, 1100, 40), index=idx)
    full = uj._rs20(stock, nas)
    assert full == pytest.approx((stock.iloc[-1] / stock.iloc[-21] - 1) * 100 - (nas.iloc[-1] / nas.iloc[-21] - 1) * 100)
    gap = nas.drop(idx[-8:-5])                                                      # 나스닥에 3일이 빠졌다: 행 기준이면 기간이 어긋난다
    j = pd.concat([stock, gap], axis=1, join="inner")
    expect = (j.iloc[-1, 0] / j.iloc[-21, 0] - 1) * 100 - (j.iloc[-1, 1] / j.iloc[-21, 1] - 1) * 100
    assert uj._rs20(stock, gap) == pytest.approx(expect)
    assert uj._rs20(stock, nas.iloc[:-1]) is None                                   # 나스닥이 종목의 마지막 날짜를 모른다
    assert uj._rs20(stock.iloc[:15], nas.iloc[:15]) is None                         # 공통 거래일 부족


def test_buy_is_held_when_nasdaq_lags_the_stock_by_a_day():
    r = review(bench=bench().iloc[:-1])
    assert r["verdict"]["code"] == "na" and "상대강도" in r["verdict"]["reason"]


# -- B단계: 진입 ATR 고정·상승형 트레일링·분할 경고 ---------------------------------------------------
def pos(df, avg=0.95, **kw):
    return {"qty": 10, "avg_price": avg * float(df["close"].iloc[-1]), "opened_on": None, **kw}


def test_levels_use_the_stored_entry_atr_not_todays_atr():
    calm = frame()
    wild = calm.copy()
    wild.iloc[-10:, wild.columns.get_loc("high")] *= 1.08
    wild.iloc[-10:, wild.columns.get_loc("low")] *= 0.92
    avg = 135.0
    p = {"qty": 1, "avg_price": avg, "opened_on": None, "entry_atr": 2.5, "tracked_from": date(2026, 9, 1)}
    a = uj.sell_review(calm, p, mkt=GOOD_MKT, today=TODAY)
    b = uj.sell_review(wild, p, mkt=GOOD_MKT, today=TODAY)
    for r in (a, b):
        assert r["levels"]["stop"] == pytest.approx(avg - uj.STOP_K * 2.5)
        assert r["levels"]["target"] == pytest.approx(avg + uj.TARGET_K * 2.5)
        assert r["levels"]["entry_atr"] == 2.5
    assert a["levels"]["atr"] != b["levels"]["atr"]                                 # 현재 ATR 은 달라도 손절선은 같다
    legacy = {"qty": 1, "avg_price": avg, "opened_on": None}
    x, y = (uj.sell_review(f, legacy, mkt=GOOD_MKT, today=TODAY) for f in (calm, wild))
    assert x["levels"]["stop"] != y["levels"]["stop"] and x["levels"]["entry_atr"] is None      # 저장된 값이 없는 옛 기록만 현재 ATR 을 쓴다
    assert any(e["label"] == "진입 ATR" and e["value"] == "저장된 값 없음" for e in x["evidence"])
    assert any("진입 ATR 이 없어" in e["threshold"] for e in x["evidence"] if e["label"].startswith("손절선"))


def test_trailing_uses_the_highest_close_since_entry_even_beyond_20_days_and_never_falls():
    idx = pd.bdate_range(end="2026-10-29", periods=140)
    c = np.concatenate([np.full(50, 100.0), np.linspace(100, 160, 30), np.linspace(160, 140, 60)])      # 고점(160)이 60거래일 전
    df = pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": 3e6}, index=idx)
    p = {"qty": 1, "avg_price": 100.0, "opened_on": idx[40].date(), "entry_atr": 3.0, "tracked_from": idx[40].date()}
    r = uj.sell_review(df, p, mkt=GOOD_MKT, today=TODAY)
    assert r["levels"]["high"] == pytest.approx(160.0)                              # 최근 20일 고점이 아니라 진입 이후 최고 종가
    assert r["levels"]["trail"] == pytest.approx(160.0 - uj.TRAIL_K * 3.0)
    trails = [uj.sell_review(df.iloc[:k], p, mkt=GOOD_MKT, today=TODAY)["levels"]["trail"] for k in range(60, 141, 5)]
    assert all(b >= a - 1e-9 for a, b in zip(trails, trails[1:]))                   # 시간이 지나도 기준선이 내려가지 않는다
    assert passed(r, "트레일링") is True and r["verdict"]["code"] == "sell"          # 160 → 140: 기준선(152.5) 아래라 해당
    old_rule_line = float(df["close"].tail(20).max()) - uj.TRAIL_K * 3.0              # 예전 방식(최근 20일 고점)이면 기준선이 내려가 안 걸렸을 상황
    assert df["close"].iloc[-1] > old_rule_line


def test_trailing_without_a_stored_entry_date_is_labeled_as_a_20_day_high():
    r = sell(avg=0.95)
    assert any("최근 20일 고점(진입일 미상)" in e["label"] for e in r["evidence"])


def test_split_suspicion_warning():
    df = frame()                                                                    # 시세 범위는 대략 100~150
    ok = uj.sell_review(df, {"qty": 1, "avg_price": 120.0, "opened_on": None}, mkt=GOOD_MKT, today=TODAY)
    assert ok["warnings"] == []
    fwd = uj.sell_review(df, {"qty": 1, "avg_price": 300.0, "opened_on": None}, mkt=GOOD_MKT, today=TODAY)     # 2:1 분할 전 평단이 그대로
    rev = uj.sell_review(df, {"qty": 1, "avg_price": 8.0, "opened_on": None}, mkt=GOOD_MKT, today=TODAY)       # 1:10 역분할 후 시세만 커진 경우
    for r in (fwd, rev):
        assert len(r["warnings"]) == 1 and "분할" in r["warnings"][0]
    since = uj.sell_review(df, {"qty": 1, "avg_price": 105.0, "opened_on": df.index[-30].date()}, mkt=GOOD_MKT, today=TODAY)
    assert since["warnings"] and "시세 범위" in since["warnings"][0]                  # 보유 기간만 본다: 그 기간 저가보다 훨씬 낮은 평단
    assert review()["warnings"] == [] and uj.buy_review(None)["warnings"] == []


# -- 추가 매수(물타기·불타기) 의견 ------------------------------------------------------------------------
def ctx(df=None, avg=0.95, qty=10, buy_kw=None, **sell_kw):
    """같은 종목의 buy/sell 리뷰와 포지션. avg 는 마지막 종가 대비 비율."""
    df = frame() if df is None else df
    price = float(df["close"].iloc[-1])
    position = {"qty": qty, "avg_price": avg * price, "opened_on": None, "entry_atr": 2.5, "tracked_from": date(2026, 9, 1)}
    kw = {"mkt": GOOD_MKT, "bench": bench(), "today": TODAY, "meta": KNOWN, **(buy_kw or {})}
    buy = uj.buy_review(df, **kw)
    sell = uj.sell_review(df, position, **{"mkt": GOOD_MKT, "today": TODAY, "meta": KNOWN, **sell_kw})
    return buy, sell, position


def test_add_opinion_pyramid_when_winning_and_the_entry_conditions_hold_again():
    buy, sell, pos = ctx(avg=0.97)
    assert buy["verdict"]["code"] == "review" and sell["verdict"]["code"] == "hold"
    r = uj.add_opinion(buy, sell, pos)
    assert r["code"] == "pyramid" and r["label"] == "불타기 검토" and "눌림" in r["reason"] and r["note"]


def test_add_opinion_avg_down_only_when_below_average_and_conditions_hold():
    df = frame()
    price = float(df["close"].iloc[-1])
    buy, sell, pos = ctx(df, avg=1.02)                                             # 평단이 현재가보다 2% 높다 = 손실 중
    assert sell["verdict"]["code"] == "hold"
    r = uj.add_opinion(buy, sell, pos)
    assert r["code"] == "avg_down" and r["label"] == "물타기 검토" and pos["avg_price"] > price
    assert "새 평단" in r["reason"]


def test_add_opinion_refuses_when_a_sell_signal_exists():
    buy, sell, pos = ctx(avg=1.4)                                                  # 손절선 아래
    assert sell["verdict"]["code"] == "sell"
    r = uj.add_opinion(buy, sell, pos)
    assert r["code"] == "avoid" and "매도 검토" in r["reason"]


def test_add_opinion_refuses_near_the_stop_line_even_if_buy_conditions_hold():
    df = frame()
    price = float(df["close"].iloc[-1])
    buy, sell, pos = ctx(df, avg=1.0)
    stop = sell["levels"]["stop"]
    pos2 = {**pos, "avg_price": price + (price - stop) * 0.0 + 0.0}                 # 평단 = 현재가
    sell2 = {**sell, "levels": {**sell["levels"], "stop": price - 0.3 * sell["levels"]["entry_atr"]}}      # 손절선이 0.3 ATR 아래로 가까움
    sell2["verdict"] = sell["verdict"]
    r = uj.add_opinion(buy, sell2, pos2)
    assert buy["verdict"]["code"] == "review" and r["code"] == "avoid" and "0.3 ATR" in r["reason"]


def test_add_opinion_holds_back_when_data_or_conditions_do_not_allow_it():
    for kw in ({"meta": {"next_earnings": date(2026, 11, 3)}},):                   # 실적 임박 = 위험 구간
        buy, sell, pos = ctx(avg=0.97, buy_kw=kw)
        r = uj.add_opinion(buy, sell, pos)
        assert buy["verdict"]["code"] == "blocked" and r["code"] == "hold" and "위험 조건" in r["reason"] and "눌림 구간에서만" not in r["reason"]   # 위험 구간이라는 이유만 전한다(조건 미충족 안내를 덧붙이지 않는다)
    buy, sell, pos = ctx(avg=0.97, buy_kw={"mkt": {"ok": False, "text": "VIX 31.0"}})        # 시장 환경 악화 → 신규 매수 조건 미충족
    r = uj.add_opinion(buy, sell, pos)
    assert buy["verdict"]["code"] == "watch" and r["code"] == "hold" and "조건이 1개 맞지 않아요" in r["reason"] and "불타기" in r["reason"]
    buy, sell, pos = ctx(frame(step=-0.3, start=170.0), avg=1.1)                    # 하락 추세 + 손실 중
    r = uj.add_opinion(buy, sell, pos)
    assert r["code"] in {"hold", "avoid"} and r["code"] != "avg_down"


def test_add_opinion_stale_sell_review_is_a_hold():
    old = frame(end="2026-10-01")
    buy, sell, pos = ctx(old, avg=0.97)
    assert sell["verdict"]["code"] == "na"
    r = uj.add_opinion(buy, sell, pos)
    assert r["code"] == "hold" and "29일 전" in r["reason"]                                 # 오래된 시세라는 이유(매도 검토의 사유)를 전한다


def test_add_opinion_simulation_numbers():
    buy, sell, pos = ctx(avg=1.05)
    r = uj.add_opinion(buy, sell, pos)
    price, avg, qty, stop = sell["price"], pos["avg_price"], pos["qty"], sell["levels"]["stop"]
    sims = [e for e in r["evidence"] if e["label"].startswith("시뮬레이션")]
    assert len(sims) == 2 and all(e.get("kind") == "info" for e in sims)
    new_avg = (qty * avg + qty * price) / (2 * qty)                                 # 100% 추가
    assert f"${new_avg:,.2f}" in sims[1]["value"] and f"{(new_avg / avg - 1) * 100:+.1f}%" in sims[1]["value"]
    assert f"${max(avg - stop, 0) * qty:,.0f} → ${max(new_avg - stop, 0) * 2 * qty:,.0f}" in sims[1]["value"]
    assert r["evidence"][0]["label"] == "내 위치" and "손실 중" in r["evidence"][0]["value"]


def test_add_opinion_does_not_mutate_inputs_and_keeps_the_buy_evidence():
    buy, sell, pos = ctx(avg=0.97)
    snapshot = (list(buy["evidence"]), dict(pos))
    r = uj.add_opinion(buy, sell, pos)
    assert (buy["evidence"], pos) == snapshot
    labels = [e["label"] for e in r["evidence"]]
    assert all(e["label"] in labels for e in buy["evidence"])                       # 신규 매수 조건 근거가 그대로 붙는다


def test_add_opinion_holds_back_when_the_earnings_date_is_unknown():
    buy, sell, pos = ctx(avg=0.97, buy_kw={"meta": {}})
    assert buy["verdict"]["code"] == "review_check"                                                  # 매수 쪽은 '실적일 확인 필요'로 구분되지만
    for avg in (0.97, 1.02):                                                                          # 추가 매수 의견은 갭 위험을 모르면 내지 않는다(불타기·물타기 모두)
        buy, sell, pos = ctx(avg=avg, buy_kw={"meta": {}})
        r = uj.add_opinion(buy, sell, pos)
        assert r["code"] == "hold" and "실적일" in r["reason"] and "보류" in r["reason"]


def test_add_opinion_states_the_basis_date_and_does_not_call_the_close_a_live_price():
    buy, sell, pos = ctx(avg=1.02)
    r = uj.add_opinion(buy, sell, pos, as_of=sell["as_of"])
    assert r["as_of"] == sell["as_of"] == "2026-10-29"
    row = next(e for e in r["evidence"] if e["label"] == "판단 기준")
    assert "2026-10-29 종가" in row["value"] and "장중 현재가가 아니에요" in row["threshold"]
    own = [e for e in r["evidence"] if e["label"].startswith(("내 위치", "판단 기준", "시뮬레이션"))]                  # 이 의견이 직접 만든 행(신규 매수 근거는 원래 종가 기준)
    text = " ".join(e["label"] + " " + e["value"] + " " + e["threshold"] for e in own) + r["note"] + r["reason"]
    text = text.replace("장중 현재가가 아니에요", "")                                                          # 현재가가 아니라는 안내 문구는 허용
    assert len(own) == 4 and "지금 가격" not in text and "현재가" not in text and "최대 손실" not in text
    sims = [e for e in r["evidence"] if e["label"].startswith("시뮬레이션")]
    assert all("2026-10-29 종가에 추가한다고 가정" in e["label"] and "도달 시 예상손실" in e["value"] and "갭" in e["threshold"] for e in sims)
    assert uj.add_opinion(buy, sell, pos)["as_of"] is None and not any(e["label"] == "판단 기준" for e in uj.add_opinion(buy, sell, pos)["evidence"])


def test_add_opinion_reason_uses_the_markets_own_condition_names():
    buy, sell, pos = ctx(avg=0.97)
    assert "상대강도" in uj.add_opinion(buy, sell, pos)["reason"]                                      # 미국 기본
    from stockwatch import add_opinion as ao
    r = ao.add_opinion(buy, sell, pos, conds=ao.KR_CONDS)
    assert "매수 점수" in r["reason"] and "상대강도" not in r["reason"]
