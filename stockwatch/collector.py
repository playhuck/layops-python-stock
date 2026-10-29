"""데이터 갱신 파이프라인: 시세(yfinance) + VIX + 수급(KRX). 동시 실행은 락으로 막는다."""
import threading
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from . import config, krx, marketdb

ROOT = Path(__file__).resolve().parent.parent
_lock = threading.Lock()
EXTRA_STOCK = {"foreign_own": krx.fetch_foreign_ownership, "market_cap": krx.fetch_market_cap,
               "short_trading": krx.fetch_short_trading, "short_balance": krx.fetch_short_balance}
BACKFILL_DAYS = 30  # 일 단위 시장 수급 보관 목표(영업일). 처음 한 번만 채우고 이후엔 신규 날짜만 호출
INDICES = {"KOSPI": ("1", "001"), "KOSDAQ": ("2", "001")}
status = {"running": False, "last_run": None, "trigger": None, "steps": []}


def _cfg() -> dict:
    return config.load()


def _yahoo(sym: str, period: str):
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.Ticker(sym).history(period=period, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"{sym}: 데이터 없음")
    df.index = df.index.tz_localize(None); df.index.name = "date"
    return df.rename(columns=str.lower).reset_index()


def refresh(trigger: str) -> bool:
    """False 면 이미 다른 갱신이 진행 중이라 건너뜀."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        cfg = _cfg()
        status.update(running=True, trigger=trigger, steps=[])

        def step_inline(name, fn):
            try:
                fn(); status["steps"].append({"name": name, "ok": True})
            except Exception as e:
                status["steps"].append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}"})

        def step(name, fn):
            try:
                fn(); status["steps"].append({"name": name, "ok": True})
            except Exception as e:  # 한 단계 실패가 나머지를 막지 않게
                status["steps"].append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}"})

        for it in cfg["watchlist"]:
            code = it["code"]
            suffix = ".KQ" if it.get("market") == "KSQ" else ".KS"
            step(f"시세 {code}", lambda: marketdb.upsert_prices(code, _yahoo(code + suffix, "2y")[["date", "open", "high", "low", "close", "volume"]]))
        step("VIX", lambda: marketdb.upsert_vix(_yahoo("^VIX", "1mo")[["date", "close"]]))

        def flows():
            c = krx.KrxClient(); c.login()
            for it in cfg["watchlist"]:
                try:
                    fl = krx.fetch_flows(c, it["code"])
                    marketdb.upsert_flows(it["code"], fl)
                    status["steps"].append({"name": f"수급 {it['code']}", "ok": True})
                    # 세부 구분은 최근 7거래일만 (하루 1회 호출)
                    marketdb.save_dataset("flows_detail", it["code"], krx.fetch_flow_detail(c, it["code"], list(fl["date"].tail(7))))
                    status["steps"].append({"name": f"수급상세 {it['code']}", "ok": True})
                    for folder, fn in EXTRA_STOCK.items():
                        step_inline(f"{folder} {it['code']}", lambda fn=fn, folder=folder: marketdb.save_dataset(
                            folder, it["code"], fn(c, it["code"])))
                except Exception as e:
                    status["steps"].append({"name": f"수급 {it['code']}", "ok": False, "error": f"{type(e).__name__}: {e}"})
        step("KRX 로그인", flows)

        def market():
            c = krx.KrxClient(); c.login()
            codes = [it["code"] for it in cfg["watchlist"]]
            fund = krx.fetch_fundamentals(c, codes)  # KRX 는 스냅샷만 주므로 날짜별 upsert 로 이력을 쌓는다
            for code, g in fund.groupby("code"):
                marketdb.save_dataset("fundamental", code, g.drop(columns="code"))
            for name, (i1, i2) in INDICES.items():
                marketdb.save_dataset("index", name, krx.fetch_index(c, i1, i2))
            idx_dates = list(marketdb.read_dataset("index", "KOSPI").index[-BACKFILL_DAYS:])
            for mkt in ("STK", "KSQ"):
                have = marketdb.read_dataset("market_flow", mkt)
                have_dates = set(have.index) if have is not None else set()
                # 없는 날짜만 백필하고, 장중 값이 바뀔 수 있는 최신 영업일은 항상 다시 받는다 (하루 최대 호출 수 제한)
                need = [d for d in idx_dates if d not in have_dates] + [idx_dates[-1]]
                need = sorted(set(need))
                marketdb.save_dataset("market_flow", mkt, krx.fetch_market_flow(c, mkt, need))
        step("시장/지수/PER·PBR", market)

        def trading():
            from .trading import service  # 지연 import: 트레이딩 미사용 환경에서도 수집은 동작
            service.after_refresh()
        step("트레이딩 제안", trading)
        status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return True
    finally:
        status["running"] = False
        _lock.release()


def refresh_async(trigger: str) -> bool:
    if status["running"]:
        return False
    threading.Thread(target=refresh, args=(trigger,), daemon=True).start()
    return True


def _seconds_until(hour: int) -> float:
    now = datetime.now()
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def start_daily(hour: int = 1):
    """앱이 켜져 있는 동안 매일 hour 시에 갱신."""
    def loop():
        while True:
            threading.Event().wait(_seconds_until(hour))
            refresh("daily-01:00")
    threading.Thread(target=loop, daemon=True).start()
