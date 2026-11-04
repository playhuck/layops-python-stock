"""표시 전용 실시간(장중) 현재가. 일봉 DB·신호·매매 제안에는 쓰지 않는다 (완성된 봉 기준 유지).

야후 1분봉의 마지막 값을 쓴다. 한국 시세는 지연될 수 있어 as_of(체결 시각)를 함께 내려 화면에 보여 준다."""
import threading
import time as _time
import warnings

import pandas as pd

from . import usmarket
from .config import load as load_config

TTL = 20.0  # 초. 갱신 버튼(fresh)은 캐시를 건너뛴다
_cache: dict = {"t": 0.0, "v": None}
_lock = threading.Lock()


def parse_quotes(raw: pd.DataFrame, symbols: list[str]) -> dict[str, dict]:
    """yf.download(interval=1m) 결과 → {심볼: {price, as_of(epoch 초)}}. 값이 없는 심볼은 뺀다."""
    out = {}
    for s in symbols:
        df = usmarket._pick(raw, s) if len(raw) else None
        if df is None:
            continue
        df = df.rename(columns=str.lower)
        if "close" not in df.columns:
            continue
        c = df["close"].dropna()
        if c.empty:
            continue
        ts = c.index[-1]
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        out[s] = {"price": float(c.iloc[-1]), "as_of": int(ts.timestamp())}
    return out


def _download(yahoo_syms: list[str]) -> dict[str, dict]:
    if not yahoo_syms:
        return {}
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(yahoo_syms, period="5d", interval="1m", auto_adjust=False, group_by="ticker", progress=False, threads=True)
    return parse_quotes(raw, yahoo_syms)


def _kr_symbols() -> dict[str, str]:
    """야후 심볼 → 종목코드"""
    return {it["code"] + (".KQ" if it.get("market") == "KSQ" else ".KS"): it["code"] for it in load_config()["watchlist"]}


def get(fresh: bool = False) -> dict:
    """{"kr": {코드: {price, as_of}}, "us": {티커: {...}}}. 조회에 실패하면 해당 시장은 빈 값 (화면은 일봉 종가를 그대로 보여준다)."""
    with _lock:
        if not fresh and _cache["v"] is not None and _time.time() - _cache["t"] < TTL:
            return _cache["v"]
        kr_map, us = _kr_symbols(), usmarket.watch_symbols()
        res = {"kr": {}, "us": {}}
        try:
            q = _download(list(kr_map))
            res["kr"] = {kr_map[s]: v for s, v in q.items()}
        except Exception:
            pass
        try:
            res["us"] = _download(us)
        except Exception:
            pass
        _cache.update(t=_time.time(), v=res)
        return res
