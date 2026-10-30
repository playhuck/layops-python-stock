"""미국(나스닥) 데이터: ① 시장 환경 지표 ② 스윙 후보 종목 수집·지표 계산.

- 갱신 시각: 미국 정규장은 한국시간 05~06시에 마감한다. 한국장 갱신(01시)은 미국 장중이라 쓰지 않고, 미국은 07시에 따로 갱신한다.
- 저장은 완성된 봉만: 마감 전 봉, 시가·고가·저가·종가 중 하나라도 빈 봉은 저장하지 않는다 (한국 시세의 NaN 사고와 같은 문제를 막는다).
- 여기서 만드는 값은 수익률/ATR/거래대금/실적일 같은 사실값이다. '매수 적기' 같은 판정은 검증 전이라 만들지 않는다.
- 시세는 prices 테이블에 티커를 code 로 넣는다 (KRX 6자리 코드와 겹치지 않음). 종목 메타(시총·공매도·실적일)는 us_meta 에 최신 1행.
"""
import re
import threading
import time as _time
import warnings
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import db, indicators as ind, marketdb

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
CLOSED_AFTER = time(16, 30)       # 정규장 마감 16:00 + 여유. 이 시각 전의 오늘 봉은 장중 값이다
MARKET = {"^IXIC": "나스닥 종합", "^NDX": "나스닥100", "^SOX": "필라델피아 반도체", "^RUT": "러셀2000(소형주)", "QQQ": "QQQ (나스닥100 ETF)",
          "IWM": "IWM (러셀2000 ETF)", "^VIX": "VIX (변동성)", "^TNX": "미 국채 10년 금리", "DX-Y.NYB": "달러 인덱스"}
LIVE = {"NQ=F": "나스닥100 선물"}   # 거의 24시간 거래라 일봉으로 저장하지 않고, 지금 값만 짧게 캐시해서 보여준다
BENCH = {"nasdaq": "^IXIC", "small": "IWM"}
MAX_WATCH = 40
FETCH_PERIOD = "2y"        # 수집 구간. 지표 입력(INPUT_ROWS)보다 길게 받아, 입력 범위가 항상 이번에 새로 받은(분할 등이 소급 반영된) 구간 안에 있게 한다
INPUT_ROWS = 400           # 지표 계산에 쓰는 최근 행 수. DB 에는 수집 구간 밖의 오래된 행이 남을 수 있어 그것이 지표에 섞이지 않게 자른다
META_TTL_HOURS = 20        # 종목 메타(시총·공매도·실적일)는 이 시간 안에 받았으면 다시 받지 않는다
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
# 사실값에 붙이는 주의 표시 기준 (휴리스틱이며 검증된 임계값이 아니다)
LOW_LIQUIDITY_USD, BIG_GAP_PCT, HIGH_SHORT_PCT, EARNINGS_DAYS = 5_000_000, 10.0, 20.0, 7

status = {"running": False, "last_run": None, "trigger": None, "steps": []}
_lock = threading.Lock()


# -- 관심 종목 ---------------------------------------------------------------------------------
def watch_list() -> list[dict]:
    return [dict(r) for r in db.get().execute("SELECT symbol,name FROM us_watchlist ORDER BY sort_order").fetchall()]


def watch_symbols() -> list[str]:
    return [w["symbol"] for w in watch_list()]


def _check_addable(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise ValueError("티커는 영문 1~5자여야 합니다 (예: SOUN)")
    items = watch_list()
    if any(i["symbol"] == symbol for i in items):
        raise ValueError("이미 관심 종목에 있습니다")
    if len(items) >= MAX_WATCH:
        raise ValueError(f"미국 관심 종목은 최대 {MAX_WATCH}개입니다")
    return symbol


def add_watch(symbol: str, name: str) -> list[dict]:
    symbol = _check_addable(symbol)
    db.get().execute("INSERT INTO us_watchlist(symbol,name) VALUES(%s,%s)", (symbol, (name.strip() or symbol)[:60]))
    return watch_list()


def add_and_collect(symbol: str, name: str = "") -> list[dict]:
    """추가 전에 야후에서 실제로 시세가 오는 티커인지 확인하고, 받은 시세는 바로 저장한다. 종목 정보(시총·실적일 등)는 뒤에서 채운다."""
    symbol = _check_addable(symbol)
    bars, errs = fetch_bars([symbol])
    if symbol not in bars:
        raise ValueError(f"야후에서 시세를 가져오지 못했어요 ({errs.get(symbol, '알 수 없음')})")
    items = add_watch(symbol, name)
    marketdb.upsert_prices(symbol, bars[symbol])
    threading.Thread(target=_collect_meta, args=(symbol,), daemon=True).start()
    return items


def _collect_meta(symbol: str):
    try:
        m = fetch_meta(symbol)
        save_meta(symbol, m)
        if m.get("name"):
            db.get().execute("UPDATE us_watchlist SET name=%s WHERE symbol=%s AND name=symbol", (m["name"][:60], symbol))   # 이름을 안 넘긴 경우만 채운다
    except Exception:
        pass                                                  # 다음 정기 갱신에서 다시 시도


def remove_watch(symbol: str) -> list[dict]:
    d = db.get()
    if d.execute("DELETE FROM us_watchlist WHERE symbol=%s", (symbol,)).rowcount == 0:
        raise KeyError(symbol)
    d.execute("DELETE FROM us_meta WHERE symbol=%s", (symbol,))
    if symbol not in MARKET:                                  # 시장 지표 심볼의 시세는 남긴다. 종목 시세는 다시 추가하면 다시 받는다
        d.execute("DELETE FROM prices WHERE code=%s", (symbol,))
    return watch_list()


# -- 수집 --------------------------------------------------------------------------------------
def complete_us_bars(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """미국 정규장 마감 전(미국 동부시간 16:30 전)의 오늘 봉은 장중 값이라 제외한다. 날짜 인덱스는 거래일(동부시간 기준)."""
    if df.empty:
        return df
    now = (now or datetime.now(ET)).astimezone(ET)
    d = np.array(df.index.date)
    return df[(d < now.date()) | ((d == now.date()) & (now.time() >= CLOSED_AFTER))]


def _pick(raw: pd.DataFrame, sym: str) -> pd.DataFrame | None:
    if isinstance(raw.columns, pd.MultiIndex):
        return raw[sym].copy() if sym in raw.columns.get_level_values(0) else None
    return raw.copy()                                         # 한 종목만 요청하면 평면 컬럼으로 오는 버전이 있다


def fetch_bars(symbols: list[str], period: str = FETCH_PERIOD, now: datetime | None = None) -> tuple[dict, dict]:
    """(심볼→완성된 일봉 DataFrame, 심볼→실패 사유). 한 종목 실패가 나머지를 막지 않는다."""
    import yfinance as yf
    out, err = {}, {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = yf.download(chunk, period=period, auto_adjust=False, group_by="ticker", progress=False, threads=True)
        except Exception as e:
            err.update({s: f"{type(e).__name__}: {str(e)[:100]}" for s in chunk})
            continue
        for s in chunk:
            df = _pick(raw, s) if len(raw) else None
            if df is None:
                err[s] = "데이터 없음"; continue
            df = df.rename(columns=str.lower)
            if not {"open", "high", "low", "close", "volume"} <= set(df.columns):
                err[s] = "컬럼 없음"; continue
            df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.index.name = "date"
            done = complete_us_bars(df, now)
            if done.empty:
                err[s] = "완성된 봉 없음"
            else:
                done.attrs["dropped"] = [x.date() for x in df.index.difference(done.index)]   # 이번 응답에서 장중(미완성)으로 걸러진 날짜
                out[s] = done
    return out, err


def persist_vix(df: pd.DataFrame, now: datetime | None = None) -> None:
    """VIX 일봉(date, close)을 완성된 것만 vix 테이블에 저장한다. 이번 응답에서 장중으로 확인된 날짜는 DB 에서도 지운다.
    응답에 없는 날짜는 건드리지 않는다 (야후가 오래된 데이터만 주거나 빈 응답을 줘도 정상 행이 지워지지 않는다)."""
    if df is None or df.empty:
        return
    d = df.copy(); d["date"] = pd.to_datetime(d["date"]); d = d.set_index("date")
    done = complete_us_bars(d, now)
    if len(done):
        marketdb.upsert_vix(done.reset_index()[["date", "close"]])
    marketdb.delete_vix([x.date() for x in d.index.difference(done.index)])


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def fetch_meta(symbol: str, today: date | None = None) -> dict:
    import yfinance as yf
    today = today or datetime.now(ET).date()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = yf.Ticker(symbol)
        info = t.info or {}
        try:
            cal = t.calendar or {}
        except Exception:
            cal = {}
    ed = [d for d in (cal.get("Earnings Date") or []) if isinstance(d, date) and d >= today]
    sp = _num(info.get("shortPercentOfFloat"))
    return {"name": info.get("shortName") or info.get("longName"), "market_cap": _num(info.get("marketCap")), "avg_volume": _num(info.get("averageVolume")), "float_shares": _num(info.get("floatShares")),
            "short_pct_float": None if sp is None else sp * 100, "beta": _num(info.get("beta")), "sector": info.get("sector"),
            "industry": info.get("industry"), "next_earnings": min(ed) if ed else None}


def save_meta(symbol: str, m: dict):
    db.get().execute(
        """INSERT INTO us_meta(symbol,market_cap,avg_volume,float_shares,short_pct_float,beta,sector,industry,next_earnings)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol) DO UPDATE SET updated_at=now(), market_cap=EXCLUDED.market_cap,
           avg_volume=EXCLUDED.avg_volume, float_shares=EXCLUDED.float_shares, short_pct_float=EXCLUDED.short_pct_float, beta=EXCLUDED.beta,
           sector=EXCLUDED.sector, industry=EXCLUDED.industry, next_earnings=EXCLUDED.next_earnings""",
        (symbol, m["market_cap"], m["avg_volume"], m["float_shares"], m["short_pct_float"], m["beta"], m["sector"], m["industry"], m["next_earnings"]))


def read_meta(symbol: str) -> dict | None:
    r = db.get().execute("SELECT * FROM us_meta WHERE symbol=%s", (symbol,)).fetchone()
    return dict(r) if r else None


def meta_fresh(symbol: str, hours: float = META_TTL_HOURS) -> bool:
    m = read_meta(symbol)
    return bool(m and m.get("updated_at") and datetime.now(timezone.utc) - m["updated_at"] < timedelta(hours=hours))


def _refresh_meta(symbol: str):
    if not meta_fresh(symbol):
        save_meta(symbol, fetch_meta(symbol))      # 실패하면 예외 → 이전 값은 그대로 남는다


def _save_vix(done: pd.DataFrame):
    marketdb.upsert_vix(done.reset_index()[["date", "close"]])
    marketdb.delete_vix(done.attrs.get("dropped", []))


def refresh_us(trigger: str, symbols: list[str] | None = None, with_meta: bool = True) -> bool:
    """False 면 이미 진행 중. symbols 를 주면 그 종목만(시장 지표는 항상 포함) 수집한다."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        status.update(running=True, trigger=trigger, steps=[])
        syms = symbols if symbols is not None else watch_symbols()

        def step(name, fn):
            try:
                fn(); status["steps"].append({"name": name, "ok": True})
            except Exception as e:
                status["steps"].append({"name": name, "ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"})

        bars, errs = fetch_bars(list(MARKET) + [s for s in syms if s not in MARKET])
        for s, df in bars.items():
            step(f"시세 {s}", lambda s=s, df=df: marketdb.upsert_prices(s, df))
        for s, e in errs.items():
            status["steps"].append({"name": f"시세 {s}", "ok": False, "error": e})
        if "^VIX" in bars:                                        # 한국 매수 판단이 쓰는 vix 테이블도 07시에 마감 값으로 맞춘다
            step("VIX", lambda: _save_vix(bars["^VIX"]))
        if with_meta:
            for s in syms:
                step(f"종목정보 {s}", lambda s=s: _refresh_meta(s))
        status["last_run"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        return True
    finally:
        status["running"] = False
        _lock.release()


def refresh_us_async(trigger: str, symbols: list[str] | None = None) -> bool:
    if status["running"]:
        return False
    threading.Thread(target=refresh_us, args=(trigger, symbols), daemon=True).start()
    return True


def _seconds_until_kst(hour: int) -> float:
    now = datetime.now(KST)
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def start_daily_us(hour: int = 7):
    """앱이 켜져 있는 동안 매일 한국시간 hour 시에 미국 데이터 갱신 (미국 정규장 마감 뒤)."""
    def loop():
        while True:
            threading.Event().wait(_seconds_until_kst(hour))
            refresh_us(f"daily-{hour:02d}:00")
    threading.Thread(target=loop, daemon=True).start()


# -- 지표 계산 (DB 의 시세에서) --------------------------------------------------------------------
def _ret(c: pd.Series, n: int) -> float | None:
    return float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100) if len(c) > n and c.iloc[-1 - n] else None


def _bench_ret(bench: pd.DataFrame | None, upto: pd.Timestamp, n: int) -> float | None:
    return None if bench is None else _ret(bench["close"].loc[:upto].dropna(), n)


def market_row(sym: str, name: str, df: pd.DataFrame) -> dict:
    d = ind.add_all(df); last = d.iloc[-1]; c = d["close"]
    above20, above60 = bool(last.close > last.ma20) if pd.notna(last.ma20) else None, bool(last.close > last.ma60) if pd.notna(last.ma60) else None
    trend = None
    if pd.notna(last.ma20) and pd.notna(last.ma60):       # 종가·20일선·60일선의 배열만으로 나눈 사실 표기
        trend = "상승 배열" if last.close > last.ma20 > last.ma60 else "하락 배열" if last.close < last.ma20 < last.ma60 else "혼조"
    return {"symbol": sym, "name": name, "date": c.index[-1].strftime("%Y-%m-%d"), "close": float(last.close),
            "chg_1d": _ret(c, 1), "chg_5d": _ret(c, 5), "chg_20d": _ret(c, 20), "rsi": _num(last.rsi),
            "above_ma20": above20, "above_ma60": above60, "trend": trend}


def _read(symbol: str) -> pd.DataFrame:
    """지표 입력용 시세: 최근 INPUT_ROWS 행만 (수집 구간 밖의 오래된 행이 지표에 섞이지 않게)."""
    return marketdb.read_prices(symbol).tail(INPUT_ROWS)


def market_overview() -> list[dict]:
    rows = []
    for sym, name in MARKET.items():
        df = _read(sym)
        if len(df) >= 2:
            rows.append(market_row(sym, name, df))
    return rows


_live = {"t": 0.0, "v": []}


def futures_change(h: pd.DataFrame, now: datetime | None = None, max_age_days: int = 5) -> dict | None:
    """나스닥100 선물의 '지금' 값과 가장 최근 미 동부 16:00 시점 값의 차이. h: 30분봉(Close). 기준을 못 찾으면 None → 화면이 카드를 숨긴다.
    야후 fast_info.previous_close 는 임의의 야간 시점 값이라 쓰지 않는다 (실제로 −2.1% 를 −0.2% 로 보여줬다).
    기준 = 15:30 시작 봉(16:00 에 끝남)의 종가. 그 봉이 이미 끝난 것 중 가장 최근 것. 현재값과 기준값은 모두 같은 종목(NQ=F)의 봉이다."""
    if h is None or h.empty:
        return None
    now = (now or datetime.now(ET)).astimezone(ET)
    c = h["Close"].astype(float).dropna()
    if c.empty:
        return None
    c.index = c.index.tz_convert(ET)
    ok = [(t.hour == 15 and t.minute == 30 and t + timedelta(minutes=30) <= now) for t in c.index]
    base = c[ok]
    if base.empty or (now.date() - base.index[-1].date()).days > max_age_days:
        return None
    b, last = float(base.iloc[-1]), float(c.iloc[-1])
    return {"symbol": "NQ=F", "name": LIVE["NQ=F"], "last": last, "base": b, "base_at": f"{base.index[-1]:%Y-%m-%d} 16:00 ET",
            "as_of": f"{c.index[-1]:%m-%d %H:%M} ET", "chg": (last / b - 1) * 100}


def live_snapshot(ttl: float = 60.0) -> list[dict]:
    """선물 '지금' 값 (60초 캐시, 참고용). 조회에 실패하거나 기준을 못 찾으면 빈 목록 — 화면은 일봉만 보여주면 된다."""
    if _time.time() - _live["t"] < ttl:
        return _live["v"]
    out = []
    try:
        import yfinance as yf
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            f = futures_change(yf.Ticker("NQ=F").history(period="5d", interval="30m", auto_adjust=False))
        out = [f] if f else []
    except Exception:
        out = []
    _live.update(t=_time.time(), v=out)
    return out


def stock_metrics(sym: str, name: str, df: pd.DataFrame, bench: dict[str, pd.DataFrame | None], meta: dict | None, today: date | None = None) -> dict:
    d = ind.add_all(df); last = d.iloc[-1]; c = d["close"]; close = float(last.close); ts = c.index[-1]
    today = today or datetime.now(ET).date()
    vol20 = df["volume"].iloc[-21:-1].mean() if len(df) > 21 else np.nan
    gaps = ((df["open"] / df["close"].shift(1) - 1) * 100).iloc[-60:].dropna()
    max_gap = float(gaps.loc[gaps.abs().idxmax()]) if len(gaps) else None
    dollar_vol = float((df["close"] * df["volume"]).iloc[-20:].mean())
    hi52, lo52 = float(df["high"].iloc[-252:].max()), float(df["low"].iloc[-252:].min())
    m = meta or {}
    out = {
        "symbol": sym, "name": name, "date": ts.strftime("%Y-%m-%d"), "close": close,
        "chg_1d": _ret(c, 1), "chg_5d": _ret(c, 5), "chg_20d": _ret(c, 20), "chg_60d": _ret(c, 60),
        "rs20_nasdaq": None, "rs60_nasdaq": None, "rs20_small": None, "rs60_small": None,   # 같은 기간 수익률 − 벤치마크 수익률 (%p)
        "rsi": _num(last.rsi), "atr_pct": _num(last.atr / close * 100), "stoch_k": _num(last.stoch_k), "macd_hist": _num(last.macd_hist),
        "bb_pos": _num((close - last.bb_low) / (last.bb_up - last.bb_low)) if pd.notna(last.bb_up) and last.bb_up != last.bb_low else None,
        "above_ma20": bool(close > last.ma20) if pd.notna(last.ma20) else None, "above_ma60": bool(close > last.ma60) if pd.notna(last.ma60) else None,
        "ma20_slope": _num(ind.slope_pct(d["ma20"], 5)) if len(d) > 30 else None,
        "vol_ratio": _num(last.volume / vol20) if pd.notna(vol20) and vol20 > 0 else None, "dollar_vol_20d": dollar_vol,
        "from_high52_pct": (close / hi52 - 1) * 100, "from_low52_pct": (close / lo52 - 1) * 100, "max_gap_60d": max_gap,
        "market_cap": m.get("market_cap"), "short_pct_float": m.get("short_pct_float"), "beta": m.get("beta"),
        "sector": m.get("sector"), "industry": m.get("industry"), "next_earnings": None,
        "meta_updated_at": m["updated_at"].isoformat() if m.get("updated_at") else None, "days_to_earnings": None, "flags": [],
    }
    for key, b in bench.items():
        for n in (20, 60):
            r, br = _ret(c, n), _bench_ret(b, ts, n)
            out[f"rs{n}_{key}"] = None if r is None or br is None else r - br
    flags = out["flags"]
    ne = m.get("next_earnings")
    if ne:
        out["next_earnings"] = ne.isoformat(); out["days_to_earnings"] = (ne - today).days
        if 0 <= out["days_to_earnings"] <= EARNINGS_DAYS:
            flags.append(f"실적 발표 D-{out['days_to_earnings']} ({ne.isoformat()}): 갭 위험")
    if dollar_vol < LOW_LIQUIDITY_USD:
        flags.append(f"유동성 낮음 (20일 평균 거래대금 ${dollar_vol / 1e6:.1f}M)")
    if max_gap is not None and abs(max_gap) >= BIG_GAP_PCT:
        flags.append(f"최근 60일 최대 갭 {max_gap:+.1f}%")
    if (m.get("short_pct_float") or 0) >= HIGH_SHORT_PCT:
        flags.append(f"공매도 비중 높음 (유통주식의 {m['short_pct_float']:.0f}%)")
    if len(df) < 120:
        flags.append(f"시세 {len(df)}일치뿐이라 60·120일선 판단이 약함")
    if (today - ts.date()).days > 5:
        flags.append(f"시세가 {(today - ts.date()).days}일 전 데이터")
    return out


def stocks_overview() -> list[dict]:
    bench = {k: (lambda df: df if len(df) else None)(_read(sym)) for k, sym in BENCH.items()}
    rows = []
    for w in watch_list():
        df = _read(w["symbol"])
        if len(df) < 25:
            rows.append({"symbol": w["symbol"], "name": w["name"], "pending": True, "flags": [], "date": None})   # 수집 전/실패
            continue
        rows.append(stock_metrics(w["symbol"], w["name"], df, bench, read_meta(w["symbol"])))
    return rows


def series(symbol: str, days: int = 120) -> list[dict]:
    """차트용 지표 시계열 (KR 의 /api/series 와 같은 형식)."""
    df = ind.add_all(_read(symbol)).tail(days).reset_index()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return df.astype(object).where(df.notna(), None).to_dict("records")


# -- 후보 발굴 (야후 스크리너) -----------------------------------------------------------------------
_discover_cache: dict = {}


def discover(min_cap: float = 3e8, max_cap: float = 2e10, min_avg_vol: float = 1e6, min_price: float = 5.0, size: int = 40) -> list[dict]:
    """나스닥(NMS/NGM/NCM) 소형·중형주 후보: 시총 범위, 3개월 평균 거래량, 최소 주가로 거른 뒤 거래량 큰 순. 10분 캐시."""
    key = (min_cap, max_cap, min_avg_vol, min_price, size)
    hit = _discover_cache.get(key)
    if hit and _time.time() - hit[0] < 600:
        return hit[1]
    import yfinance as yf
    E = yf.EquityQuery
    q = E("and", [E("is-in", ["exchange", "NMS", "NGM", "NCM"]), E("btwn", ["intradaymarketcap", min_cap, max_cap]),
                  E("gt", ["avgdailyvol3m", min_avg_vol]), E("gt", ["intradayprice", min_price])])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = yf.screen(q, sortField="avgdailyvol3m", sortAsc=False, size=size)
    rows = [{"symbol": x.get("symbol"), "name": x.get("shortName") or x.get("longName") or x.get("symbol"), "market_cap": _num(x.get("marketCap")),
             "price": _num(x.get("regularMarketPrice")), "avg_volume": _num(x.get("averageDailyVolume3Month")),
             "exchange": x.get("exchange")}
            for x in r.get("quotes", []) if x.get("symbol") and SYMBOL_RE.match(x["symbol"])]
    _discover_cache[key] = (_time.time(), rows)
    if len(_discover_cache) > 20:
        _discover_cache.pop(next(iter(_discover_cache)))
    return rows
