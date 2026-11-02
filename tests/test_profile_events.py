import numpy as np
import pytest

from stockwatch.research.profile_events import forward_outcomes, zone_after


def bars(n, o=100.0, h=101.0, l=99.0, c=100.0):
    f = lambda v: np.full(n, float(v))
    return f(o), f(h), f(l), f(c)


def test_mfe_mae_in_atr_units_and_horizons():
    o, h, l, c = bars(20)
    h[3], l[4] = 106.0, 97.0                       # i=2 → 체결 봉은 3
    r = forward_outcomes(o, h, l, c, 2, atr=2.0, entry=100.0)
    assert r["mfe5"] == pytest.approx(3.0) and r["mae5"] == pytest.approx(1.5)
    assert r["close5"] == 100.0 and r["first_cons"] == "none"


def test_entry_bar_is_included_in_the_window():
    o, h, l, c = bars(20)
    h[3] = 110.0
    assert forward_outcomes(o, h, l, c, 2, 2.0, 100.0)["mfe5"] == pytest.approx(5.0)


def test_returns_none_when_forward_bars_are_missing_or_atr_invalid():
    o, h, l, c = bars(12)
    assert forward_outcomes(o, h, l, c, 5, 2.0, 100.0) is None            # 5+10 ≥ 12
    assert forward_outcomes(*bars(30), 5, 0.0, 100.0) is None
    assert forward_outcomes(*bars(30), 5, float("nan"), 100.0) is None


def test_same_bar_stop_and_target_gives_both_scenarios():
    o, h, l, c = bars(30)
    h[7], l[7] = 108.0, 95.0                       # 손절 96, 목표 107 둘 다 닿음
    r = forward_outcomes(o, h, l, c, 5, atr=2.0, entry=100.0)
    assert (r["first_cons"], r["first_opt"]) == ("stop", "target")


def test_first_touch_decides_and_gap_through_stop_counts():
    o, h, l, c = bars(30)
    h[8] = 108.0                                   # 목표가(107)에 먼저 도달
    l[9] = 90.0
    r = forward_outcomes(o, h, l, c, 5, 2.0, 100.0)
    assert (r["first_cons"], r["first_opt"]) == ("target", "target")
    o2, h2, l2, c2 = bars(30)
    o2[7], l2[7] = 90.0, 89.0                      # 시가부터 손절선 아래로 갭
    assert forward_outcomes(o2, h2, l2, c2, 5, 2.0, 100.0)["first_cons"] == "stop"


def test_touch_after_horizon_is_ignored():
    o, h, l, c = bars(40)
    l[20] = 50.0
    assert forward_outcomes(o, h, l, c, 5, 2.0, 100.0)["first_cons"] == "none"     # 5+10=15 까지만 본다


def closes(vals):
    return np.array(vals, dtype=float)


def test_zone_reject_break_stall_and_unreached():
    lo, hi = 110.0, 120.0
    base = [100.0] * 3
    assert zone_after(closes(base + [111, 105, 105, 105, 105, 105] + [100] * 10), 2, lo, hi)["outcome"] == "reject"
    assert zone_after(closes(base + [111, 115, 121, 121, 121, 121] + [100] * 10), 2, lo, hi)["outcome"] == "break"
    assert zone_after(closes(base + [111, 115, 115, 115, 115, 115] + [100] * 10), 2, lo, hi)["outcome"] == "stall"
    assert zone_after(closes(base + [105] * 14), 2, lo, hi) == {"reached": False, "day": None, "outcome": None}


def test_zone_entry_close_above_the_zone_is_a_break_and_first_resolution_wins():
    lo, hi = 110.0, 120.0
    assert zone_after(closes([100, 100, 100, 125] + [100] * 10), 2, lo, hi)["outcome"] == "break"
    assert zone_after(closes([100, 100, 100, 111, 105, 125] + [100] * 10), 2, lo, hi)["outcome"] == "reject"   # 먼저 반락


def test_zone_boundaries_close_equal_to_lo_counts_as_entry_and_hi_is_not_break():
    lo, hi = 110.0, 120.0
    r = zone_after(closes([100, 100, 100, 110, 120, 120, 120, 120, 120] + [100] * 5), 2, lo, hi)
    assert r["reached"] and r["day"] == 3 and r["outcome"] == "stall"          # 종가 = 상단은 돌파 아님


def test_zone_censored_when_resolution_window_is_cut_off():
    lo, hi = 110.0, 120.0
    assert zone_after(closes([100, 100, 100, 111, 115, 115]), 2, lo, hi)["outcome"] == "censored"
    assert zone_after(closes([100, 100, 100, 100]), 2, lo, hi)["reached"] is False
