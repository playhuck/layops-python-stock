import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from stockwatch import volume_profile as vp


def frame(n=60, lo=95.0, hi=105.0, vol=1000.0, close=None):
    idx = pd.bdate_range("2026-01-02", periods=n)
    df = pd.DataFrame({"open": (lo + hi) / 2, "high": hi, "low": lo, "close": (lo + hi) / 2, "volume": vol}, index=idx)
    if close is not None:
        df.iloc[-1, df.columns.get_loc("close")] = close
    return df


def test_range_overlap_splits_volume_by_overlap_length():
    edges = np.array([0.0, 10.0, 20.0, 30.0])
    out = vp._allocate(np.array([5.0]), np.array([25.0]), np.array([200.0]), np.array([15.0]), edges, "range_overlap")
    assert out.tolist() == pytest.approx([50.0, 100.0, 50.0])      # 길이 5:10:5 → 200 을 1:2:1 로


def test_flat_bar_goes_entirely_to_its_bin_and_total_is_conserved():
    edges = np.array([0.0, 10.0, 20.0])
    out = vp._allocate(np.array([12.0, 3.0]), np.array([12.0, 18.0]), np.array([70.0, 30.0]), np.array([12.0, 10.0]), edges, "range_overlap")
    assert out.sum() == pytest.approx(100.0)
    assert out[1] >= 70.0                                           # 고가 == 저가인 봉은 통째로 해당 구간
    assert vp._allocate(np.array([12.0, 3.0]), np.array([12.0, 18.0]), np.array([70.0, 30.0]), np.array([12.0, 9.0]), edges, "close").tolist() == [30.0, 70.0]


def test_flat_bar_at_the_top_edge_stays_in_last_bin():
    edges = np.array([0.0, 10.0, 20.0])
    assert vp._allocate(np.array([20.0]), np.array([20.0]), np.array([5.0]), np.array([20.0]), edges, "range_overlap").tolist() == [0.0, 5.0]


def test_unknown_allocation_raises():
    with pytest.raises(ValueError):
        vp._allocate(np.array([1.0]), np.array([2.0]), np.array([1.0]), np.array([1.5]), np.array([0.0, 3.0]), "nope")


def test_zones_merge_adjacent_thick_bins_but_do_not_requalify_after_merge():
    mk = lambda mults: [{"lo": float(i), "hi": float(i + 1), "volume": m, "share": m / 10, "mult": m} for i, m in enumerate(mults)]
    z = vp._zones(mk([1.6, 1.6, 0.5, 1.6]), 1.5)
    assert [(x["lo"], x["hi"], x["n_bins"]) for x in z] == [(0.0, 2.0, 2), (3.0, 4.0, 1)]
    assert z[0]["volume"] == pytest.approx(3.2)                      # 영역 거래량 = 구성 구간 합
    assert vp._zones(mk([1.4, 1.4, 1.4]), 1.5) == []                  # 합치면 4.2배지만 개별 구간이 기준 미달이면 영역이 아니다


def test_compute_reports_nearest_zone_edges_and_atr_distance():
    df = frame(60, 90.0, 110.0, vol=100.0)
    # 100~110 에 거래량이 몰린 구간(고가 110 저가 100) + 가장 아래 90 을 한 번 찍은 봉
    df.iloc[:, df.columns.get_loc("low")] = 100.0
    df.iloc[0, df.columns.get_loc("low")] = 90.0
    df.iloc[-1, df.columns.get_loc("close")] = 95.0                  # 현재가는 매물대 아래
    r = vp.compute(df, window=60, bins=4, thick=1.5)
    assert r["ok"] and r["meta"]["bars_used"] == 60 and r["meta"]["method_version"] == vp.METHOD_VERSION
    assert r["meta"]["last_date"] == df.index[-1].strftime("%Y-%m-%d")
    up = r["up"]
    assert up["lo"] == pytest.approx(100.0) and up["hi"] == pytest.approx(110.0)
    assert up["enter_pct"] == pytest.approx((100 - 95) / 95 * 100)    # 진입 거리는 영역 하단까지
    assert up["break_pct"] == pytest.approx((110 - 95) / 95 * 100)    # 돌파 거리는 영역 상단까지
    assert up["enter_atr"] == pytest.approx((100 - 95) / r["atr"])
    assert r["down"] is None and r["inside"] is None


def test_price_equal_to_zone_boundary_counts_as_inside():
    df = frame(60, 100.0, 110.0, vol=100.0, close=100.0)
    df.iloc[0, df.columns.get_loc("low")] = 90.0
    r = vp.compute(df, window=60, bins=2, thick=1.5)                  # 구간 [90,100] [100,110]
    assert r["ok"] and r["inside"] is not None and r["up"] is None and r["down"] is None
    assert r["inside"]["lo"] == pytest.approx(100.0)


def test_zone_below_price_uses_its_upper_edge():
    df = frame(60, 90.0, 100.0, vol=100.0)
    df.iloc[-1, df.columns.get_loc("high")] = 110.0
    df.iloc[-1, df.columns.get_loc("close")] = 108.0
    r = vp.compute(df, window=60, bins=4, thick=1.5)
    assert r["ok"] and r["up"] is None and r["down"] is not None
    assert r["down"]["hi"] == pytest.approx(100.0) and r["down"]["enter_pct"] < 0     # 아래 영역은 상단까지, 부호는 음수


def test_bad_bars_are_excluded_and_counted():
    df = frame(60)
    df.iloc[3, df.columns.get_loc("volume")] = 0
    df.iloc[4, df.columns.get_loc("volume")] = np.nan
    df.iloc[5, df.columns.get_loc("high")] = 1.0                      # 고가 < 저가
    r = vp.compute(df, window=60)
    assert r["ok"] and r["meta"]["bars_excluded"] == 3 and r["meta"]["bars_used"] == 57


@pytest.mark.parametrize("make,why", [
    (lambda: frame(20), "거래일 부족"),
    (lambda: frame(60).iloc[0:0], "거래 데이터 없음"),
    (lambda: frame(60).assign(volume=0.0), "거래일 부족"),
])
def test_unavailable_cases_return_reason_not_exception(make, why):
    r = vp.compute(make())
    assert r["ok"] is False and why in r["reason"]


def test_invalid_last_bar_is_unavailable():
    df = frame(60)
    df.iloc[-1, df.columns.get_loc("volume")] = np.nan
    assert vp.compute(df)["reason"].startswith("마지막 봉")


def test_flat_price_range_is_unavailable():
    df = frame(60, 100.0, 100.0)
    assert vp.compute(df)["reason"] == "가격 범위가 없음"


def test_result_is_strict_json_and_input_not_mutated():
    df = frame(80)
    before = df.copy()
    r = vp.compute(df)
    json.dumps(r, allow_nan=False)
    pd.testing.assert_frame_equal(df, before)
    assert sum(b["volume"] for b in r["bins"]) == pytest.approx(r["total_volume"])


def test_total_volume_matches_valid_bars_for_both_allocations():
    df = frame(80, 95.0, 105.0, vol=1000.0)
    for a in ("range_overlap", "close"):
        assert vp.compute(df, window=60, allocation=a)["total_volume"] == pytest.approx(60 * 1000.0)


def test_kr_completed_bars_cutoff_is_kst_1540():
    df = frame(3)
    today = df.index[-1]
    kst = vp.KST
    mk = lambda h, m, day=today: datetime(day.year, day.month, day.day, h, m, tzinfo=kst)
    assert len(vp.complete_kr_bars(df, mk(15, 39))) == 2             # 장 마감 직후까지는 오늘 봉이 미완성
    assert len(vp.complete_kr_bars(df, mk(15, 40))) == 3
    assert len(vp.complete_kr_bars(df, mk(9, 5))) == 2
    assert len(vp.complete_kr_bars(df, mk(9, 5, today + pd.Timedelta(days=3)))) == 3     # 다음 주에 보면 모두 완성
    assert vp.complete_kr_bars(df.iloc[0:0]).empty
