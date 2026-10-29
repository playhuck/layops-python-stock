import numpy as np
import pandas as pd

from stockwatch import indicators as ind
from stockwatch.signals import analyze


def make_df(closes, vol=1000):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(end="2026-10-28", periods=len(closes))
    return pd.DataFrame({"open": closes * 0.998, "high": closes * 1.01, "low": closes * 0.99,
                         "close": closes, "volume": np.full(len(closes), float(vol))}, index=idx)


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == 100
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(down).iloc[-1] < 1


def test_sma_matches_manual():
    s = pd.Series(range(1, 11), dtype=float)
    assert ind.sma(s, 5).iloc[-1] == 8.0


def test_bollinger_contains_price_in_flat_noise():
    rng = np.random.default_rng(0)
    c = pd.Series(100 + rng.normal(0, 1, 100))
    up, mid, low = ind.bollinger(c)
    assert (up.dropna() > low.dropna()).all()


def test_stochastic_range():
    df = make_df(100 + np.sin(np.arange(80)) * 5)
    k, d = ind.stochastic_slow(df)
    assert k.dropna().between(0, 100).all() and d.dropna().between(0, 100).all()


def test_obv_accumulates_on_up_days():
    df = make_df([10, 11, 12, 11])
    assert ind.obv(df).tolist() == [0, 1000, 2000, 1000]


def test_obv_bullish_divergence():
    # 가격은 완만한 횡보(미세 하락)인데, 상승일 거래량이 하락일보다 훨씬 큰 경우
    n = 60
    close = 100 + np.tile([0.3, -0.25], n // 2).cumsum()
    df = make_df(close)
    df["volume"] = np.where(df["close"].diff() > 0, 3000.0, 500.0)
    assert ind.obv_divergence(df)["bullish"]


def test_analyze_needs_history():
    try:
        analyze("X", "x", make_df(np.linspace(100, 110, 50)))
    except ValueError:
        return
    raise AssertionError("데이터 부족 시 ValueError 가 나야 한다")


def test_uptrend_pullback_scores_buy():
    base = np.linspace(100, 200, 200)
    pullback = np.linspace(200, 193, 5)  # 상승 추세 속 단기 조정
    df = make_df(np.concatenate([base, pullback]))
    r = analyze("X", "x", df, vix=14)
    assert r.buy_score >= 25
    assert any("20일선 우상향" in x for x in r.reasons)


def test_downtrend_never_buy():
    df = make_df(np.linspace(200, 100, 220))
    r = analyze("X", "x", df, vix=14)
    assert "매수 적기" not in r.verdict


def test_vix_fear_downgrades_buy():
    base = np.linspace(100, 200, 200)
    df = make_df(np.concatenate([base, np.linspace(200, 193, 5)]))
    calm = analyze("X", "x", df, vix=14).buy_score
    fear = analyze("X", "x", df, vix=35).buy_score
    assert fear < calm
