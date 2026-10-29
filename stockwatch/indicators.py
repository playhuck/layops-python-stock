"""기술적 지표 계산. 입력은 컬럼 open/high/low/close/volume 을 가진 일봉 DataFrame (오름차순 날짜 인덱스)."""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def stochastic_slow(df: pd.DataFrame, n: int = 14, k_smooth: int = 3, d_smooth: int = 3):
    """Slow stochastic: slow %K = fast %K 의 3일 SMA, %D = slow %K 의 3일 SMA."""
    ll = df["low"].rolling(n).min()
    hh = df["high"].rolling(n).max()
    fast_k = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    k = fast_k.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def slope_pct(s: pd.Series, n: int) -> float:
    """최근 n개 값에 대한 선형회귀 기울기를 평균값 대비 일일 % 로 환산."""
    y = s.dropna().tail(n).to_numpy(dtype=float)
    if len(y) < n or np.isclose(y.mean(), 0):
        return float("nan")
    x = np.arange(len(y))
    return float(np.polyfit(x, y, 1)[0] / abs(y.mean()) * 100)


def obv_divergence(df: pd.DataFrame, n: int = 20) -> dict:
    """가격은 횡보/약간 하락인데 OBV 는 우상향이면 매집 의심 (bullish).
    반대로 가격은 오르는데 OBV 가 하락이면 분산 의심 (bearish)."""
    o = obv(df)
    p_slope = slope_pct(df["close"], n)           # 일일 % 변화율
    # OBV 는 부호가 바뀔 수 있어 정규화: 구간 거래량 합으로 나눈 기울기
    vol_sum = df["volume"].tail(n).sum()
    y = o.tail(n).to_numpy(dtype=float)
    o_slope = float(np.polyfit(np.arange(len(y)), y, 1)[0] * n / vol_sum * 100) if vol_sum and len(y) == n else float("nan")
    # o_slope: 구간 전체에서 OBV 변화가 구간 거래량의 몇 % 인지
    bullish = (not np.isnan(p_slope)) and p_slope <= 0.15 and o_slope >= 10
    bearish = (not np.isnan(p_slope)) and p_slope >= 0.15 and o_slope <= -10
    return {"price_slope_pct_day": p_slope, "obv_slope_pct_of_vol": o_slope,
            "bullish": bool(bullish), "bearish": bool(bearish)}


def volume_analysis(df: pd.DataFrame, days: int = 7) -> dict:
    """최근 N일 거래량 분석: 20일 평균 대비 배율, 상승일/하락일 거래량 비교, 일별 내역."""
    recent = df.tail(days)
    base = df["volume"].iloc[:-days].tail(20).mean()
    avg_recent = recent["volume"].mean()
    up = recent[recent["close"] > recent["close"].shift(1).fillna(recent["open"])]["volume"].sum()
    down = recent["volume"].sum() - up
    rows = [{"date": str(i.date()), "volume": int(r.volume), "close": float(r.close),
             "ratio_vs_base": float(r.volume / base) if base else None}
            for i, r in recent.iterrows()]
    return {"avg_recent": float(avg_recent), "base_avg_20d": float(base) if base == base else None,
            "ratio": float(avg_recent / base) if base else None,
            "up_volume": float(up), "down_volume": float(down),
            "up_down_ratio": float(up / down) if down else None, "daily": rows}


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR (평균 진폭): 종목별 변동성에 맞춘 손절/익절 폭의 기준."""
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for n in (5, 20, 60, 120):
        d[f"ma{n}"] = sma(d["close"], n)
    d["rsi"] = rsi(d["close"])
    d["stoch_k"], d["stoch_d"] = stochastic_slow(d)
    d["macd"], d["macd_signal"], d["macd_hist"] = macd(d["close"])
    d["bb_up"], d["bb_mid"], d["bb_low"] = bollinger(d["close"])
    d["obv"] = obv(d)
    d["atr"] = atr(d)
    return d
